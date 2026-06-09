import json
import time
from collections.abc import Iterator
from queue import Queue
from threading import Thread

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, selectinload

from app import models, schemas
from app.config import get_settings
from app.database import SessionLocal, get_db, init_db
from app.services.agent_monitor import (
    cleanup_empty_runs,
    create_trace_run,
    finish_trace_run,
    get_monitor_settings_payload,
    monitor_event_stream,
    update_monitor_settings,
)
from app.services.agents import KeeperSupervisor
from app.services.assistant_agent import GameAssistantAgent
from app.services.characters import ensure_character_attributes
from app.services.debug_events import emit_debug
from app.services.importer import ensure_default_scenario, import_default_content
from app.services.inventory import sync_character_inventory_to_session
from app.services.image_generator import ImageGenerator
from app.services.retrieval import RetrievalService
from app.services.story_state import ensure_story_state
from app.utils import resolve_project_path

# 【阅读顺序 4：后端 HTTP API】
# 这个文件是“Web 请求”和“游戏业务”的连接层：
# 1. 前端请求 /coc/api/characters、/sessions、/actions/stream。
# 2. FastAPI 根据下面的 @router.get / @router.post 找到对应函数。
# 3. 普通接口直接返回 JSON；流式接口用 StreamingResponse 持续返回 NDJSON。
# 4. 真正的守秘人推理在 KeeperSupervisor.run_turn，也就是 backend/app/services/agents/supervisor.py。
router = APIRouter(prefix="/api")  # router = 路由：FastAPI 路由对象，所有API端点注册在此
_agent: KeeperSupervisor | None = None  # _agent = 守秘人调度器单例：进程内复用，避免重复初始化LLM和检索服务
_assistant_agent: GameAssistantAgent | None = None  # _assistant_agent = 游戏助手单例：进程内复用


def get_agent() -> KeeperSupervisor:
    # KeeperSupervisor 初始化较重，使用进程内单例复用各子 Agent、LLM 与检索服务。
    # 初学者注意：这里不是每次请求都 new 一个 Supervisor，否则会重复构建客户端，浪费资源。
    global _agent
    if _agent is None:
        _agent = KeeperSupervisor()
    return _agent


def get_assistant_agent() -> GameAssistantAgent:
    global _assistant_agent
    if _assistant_agent is None:
        _assistant_agent = GameAssistantAgent()
    return _assistant_agent


def ensure_current_character_attributes(db: Session) -> models.Scenario:
    # 每次读取角色前同步默认剧本与预设角色属性，避免资料导入后前端拿到旧结构。
    scenario = ensure_default_scenario(db)
    ensure_character_attributes(db, scenario, resolve_project_path(get_settings().character_dir))
    return scenario


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "正常"}


@router.post("/init")
def initialize_database() -> dict[str, str]:
    init_db()
    return {"status": "已初始化"}


@router.post("/import", response_model=schemas.ImportResponse)
def import_content(payload: schemas.ImportRequest, db: Session = Depends(get_db)) -> dict:
    return import_default_content(db, reset_chroma=payload.reset_chroma, include_characters=payload.import_characters)


@router.get("/characters", response_model=list[schemas.CharacterOut])
def list_characters(db: Session = Depends(get_db)) -> list[models.Character]:
    scenario = ensure_current_character_attributes(db)
    return db.query(models.Character).filter(models.Character.scenario_id == scenario.id).order_by(models.Character.archetype).all()


@router.post("/sessions", response_model=schemas.SessionOut)
def create_session(payload: schemas.SessionCreate, db: Session = Depends(get_db)) -> schemas.SessionOut:
    # 【Web 流程 8】创建会话：前端选择角色后调用这里，后端创建 GameSession 并返回页面需要的会话视图。
    scenario = ensure_current_character_attributes(db)
    character = None
    if payload.character_id:
        character = db.get(models.Character, payload.character_id)
    # 未指定角色时优先使用推荐的“调查局探员”，否则退回任意可用角色。
    if character is None:
        character = db.query(models.Character).filter(models.Character.scenario_id == scenario.id, models.Character.archetype == "调查局探员").one_or_none()
    if character is None:
        character = db.query(models.Character).filter(models.Character.scenario_id == scenario.id).first()
    if character is None:
        raise HTTPException(status_code=400, detail="没有可用角色。请先调用 /coc/api/import 导入资料。")
    session = models.GameSession(scenario_id=scenario.id, character_id=character.id, title=payload.title)
    # 新会话立即初始化结构化剧情状态，后续回合只在此结构上增量推进。
    session.state = ensure_story_state({}, session.current_location, session.current_scene, session.current_time)
    db.add(session)
    db.flush()
    sync_character_inventory_to_session(db, session, character)
    db.commit()
    return build_session_out(db, session.id)


@router.get("/sessions", response_model=list[schemas.SessionOut])
def list_sessions(db: Session = Depends(get_db)) -> list[schemas.SessionOut]:
    ensure_current_character_attributes(db)
    # 预加载前端展示所需关联对象，减少序列化时的额外数据库查询。
    sessions = (
        db.query(models.GameSession)
        .options(
            selectinload(models.GameSession.character),
            selectinload(models.GameSession.clues),
            selectinload(models.GameSession.inventory_items),
            selectinload(models.GameSession.flags),
            selectinload(models.GameSession.turn_logs),
        )
        .order_by(models.GameSession.updated_at.desc())
        .limit(20)
        .all()
    )
    return [build_session_out(db, session.id) for session in sessions]


@router.get("/sessions/{session_id}", response_model=schemas.SessionOut)
def get_session(session_id: str, db: Session = Depends(get_db)) -> schemas.SessionOut:
    ensure_current_character_attributes(db)
    return build_session_out(db, session_id)


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, db: Session = Depends(get_db)) -> dict[str, int | str]:
    session = db.get(models.GameSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="未找到指定会话")
    deleted_memory_chunks = 0
    try:
        # 删除数据库会话时同步清理向量库中的会话记忆，避免旧记忆影响新游戏。
        deleted_memory_chunks = RetrievalService().delete_where("session_memory_chunks", {"session_id": session_id})
    except Exception:
        deleted_memory_chunks = 0
    db.delete(session)
    db.commit()
    return {"status": "已删除", "deleted_memory_chunks": deleted_memory_chunks}


@router.post("/sessions/{session_id}/actions", response_model=schemas.ActionResponse)  # 注册 POST /api/sessions/{id}/actions → 非流式行动提交
def submit_action(session_id: str, payload: schemas.PlayerActionIn, db: Session = Depends(get_db)) -> schemas.ActionResponse:
    # 【Web 流程 9】非流式行动接口：适合调试或脚本调用；页面主要使用下面的 stream 版本。
    #                                                    ↑
    # FastAPI 会：① 从 URL 路径提取 session_id（str）
    #           ② 从请求体 JSON 解析 payload（PlayerActionIn，含 message 字段 min_length=1 max_length=4000）
    #           ③ 通过 Depends(get_db) 注入数据库会话 db
    #           ④ 用 response_model=ActionResponse 校验返回值（narration/options/dice_results/session 等）

    # 1. 会话存在性校验 —— 若 session_id 在 GameSession 表中查不到，直接返回 404
    if db.get(models.GameSession, session_id) is None:
        raise HTTPException(status_code=404, detail="未找到指定会话")

    # 2. 为本次行动创建一个 Agent 追踪运行记录（trace_run），用于 /monitor 页面查看 Agent 执行轨迹
    #    source="action" 标记来源为玩家行动；metadata 记录 stream 模式与玩家原始消息
    trace_recorder = create_trace_run(session_id=session_id, source="action", metadata={"stream": False, "message": payload.message})

    # 3. 调用守秘人（KeeperSupervisor）执行完整回合 —— 同步等待，一次性拿到所有结果
    try:
        result = get_agent().run_turn(                      # get_agent() 返回 KeeperSupervisor 进程内单例，避免重复初始化 LLM
            db, session_id, payload.message,                # db=数据库会话, session_id=当前会话, payload.message=玩家输入的文本
            trace_recorder=trace_recorder                   # trace_recorder 让 Supervisor 在执行过程中记录每个 Agent 的输入/输出
        )
        finish_trace_run(trace_recorder, "success")         # 4a. 回合正常结束 → 标记追踪记录状态为 "success"
    except Exception as exc:
        finish_trace_run(trace_recorder, "error", str(exc)) # 4b. 回合异常 → 标记追踪记录状态为 "error" 并记录异常信息
        raise                                               # 重新抛出异常，让 FastAPI 自动生成 500 错误响应

    # 5. 可选场景图片生成 —— 当守秘人判定本回合有值得配图的关键场景时 needs_image=True
    if result.get("needs_image"):
        session = db.get(models.GameSession, session_id)    # 重新从数据库获取最新会话状态（回合中可能已被 Supervisor 修改）
        if session and session.turn_logs:                   # 确保会话存在且至少有一条回合日志
            latest_turn = max(session.turn_logs, key=lambda log: log.turn_index)  # 取 turn_index 最大的一条回合日志（即本回合刚创建的）
            image_gen = ImageGenerator()                    # 图片生成器实例 —— 内部封装了 Stable Diffusion / DALL·E 等后端
            image_url = image_gen.generate_and_save(        # 根据 narration 文本 + scene_type 生成图片并持久化到数据库
                db, latest_turn.id,                         # db + turn_id 用于关联图片到具体回合
                result["narration"],                        # narration 文本作为图片生成的 prompt 基础
                result.get("image_scene_type", "")          # scene_type: "new_scene"→16:9 宽幅, 其他→1:1 方图
            )
            if image_url:
                result["image_url"] = image_url              # 5a. 生成成功 → 把图片URL挂到返回结果里
                result["image_metadata"] = latest_turn.image_metadata  # 同时带回元数据（如生成参数、宽高比等）
            # 5b. 生成失败 → image_url 为 None/空，result 中就不带图片字段，前端不显示图

    # 6. 组装并返回 ActionResponse —— 把 Supervisor 的内部结果转成前端期望的稳定结构
    #    build_action_response 做了几件事：重新查询最新会话、拼装 session/clues/turns 等视图数据
    return build_action_response(db, session_id, result)


@router.post("/sessions/{session_id}/actions/stream")
def submit_action_stream(session_id: str, payload: schemas.PlayerActionIn, db: Session = Depends(get_db)) -> StreamingResponse:
    # 【Web 流程 10】流式行动接口：玩家输入会在这里进入 KeeperSupervisor，也就是当前多 Agent 回合链路。
    # 对学习者来说，这个接口很值得精读，因为它把”Web 请求”和”Agent 回合”真正接了起来：
    # 1. 浏览器发来一句玩家输入。
    # 2. 这里开启后台线程运行 Supervisor。
    # 3. Supervisor 在执行过程中不断把调试事件放进队列。
    # 4. event_stream() 再把这些事件编码成 NDJSON，持续推给前端。
    #                                                     ↑
    # 返回类型 StreamingResponse（非普通 dict/JSON）—— 告诉 FastAPI 这是一个”长连接流式响应”，
    # 不会等全部数据准备好再返回，而是通过生成器逐块发送 NDJSON（每行一个JSON），让前端实时收到增量更新。
    # 相比上面的 submit_action（非流式），这里的核心区别是：
    #   非流式 → 同步等待 run_turn 全部完成 → 一次性返回 ActionResponse JSON
    #   流式   → 后台线程跑 run_turn，主线程通过 Queue 中转，逐步 yield NDJSON 事件给浏览器

    # 1. 会话存在性校验 —— 与非流式接口一致，优先校验避免无效请求进入后台线程
    if db.get(models.GameSession, session_id) is None:
        raise HTTPException(status_code=404, detail="未找到指定会话")

    # 2. 定义内部生成器 event_stream —— 这是 StreamingResponse 的核心：
    #    FastAPI 收到 StreamingResponse 后会调用这个生成器，每次 yield 就把数据 flush 给浏览器，
    #    直到生成器 return（正常结束）或抛出异常（异常结束）
    def event_stream() -> Iterator[str]:  # Iterator[str] = 字符串迭代器，每次 yield 一个 JSON 行文本
        try:
            # 3. 立即发送 start 事件 —— 让前端第一时间知道连接已建立、可以开始显示加载状态
            yield encode_stream_event({"type": "start"})  # → 前端收到 {“type”:”start”}，挂起等待状态

            # 4. 创建事件队列（Queue = 线程安全的 FIFO 队列）
            #    这是生产者-消费者模式的”中转站”：
            #    - 生产者 = 后台线程 (run_agent) 把 run_turn 的中间结果和调试事件 put 进队列
            #    - 消费者 = 当前生成器线程，不断从队列 get()，然后 yield 给浏览器
            events = Queue()

            # 5. 定义调试事件回调 enqueue_debug —— 双 lambda 包装：
            #    - 外层 debug_emit 在 supervisor 中被各个 Agent 调用，传入 phase/name/status/message
            #    - 内层 enqueue_debug 将事件包装为 {“type”:”debug”,”event”:{...}} 放入队列
            #    这样 Supervisor 内部产生的一切调试信息都能实时流转到前端监视器
            def enqueue_debug(event: dict) -> None:
                events.put({"type": "debug", "event": event})

            # 6. 定义后台工作函数 run_agent —— 将在独立线程中执行
            def run_agent() -> None:
                # 6a. 创建线程专属的数据库会话（worker_db）
                #     SQLAlchemy Session 不是线程安全的，不能跨线程共享外层 db。
                #     所以后台线程创建一个新的 SessionLocal()，生命周期完全在线程内：
                #     try 中创建 → 使用 → finally 中关闭
                worker_db = SessionLocal()

                # 6b. 为本次流式行动创建监控追踪记录（与 /monitor 页面联动）
                #     metadata 中标记 stream=True 区分于非流式接口
                trace_recorder = create_trace_run(session_id=session_id, source="action", metadata={"stream": True, "message": payload.message})

                try:
                    # 6c. 发送第一条调试事件 → 前端监视器可见”守秘人回合开始”
                    emit_debug(enqueue_debug, phase="stream", name="action_stream", status="start", message="守秘人回合开始。")

                    # 6d. ★ 核心调用：执行守秘人完整回合（6 阶段多 Agent 链路）
                    #     与非流式接口不同，这里传了 debug_emit=enqueue_debug，
                    #     这样 Supervisor 内部每个子 Agent 的执行步骤都会实时推入 Queue → yield 给前端
                    result = get_agent().run_turn(worker_db, session_id, payload.message, debug_emit=enqueue_debug, trace_recorder=trace_recorder)

                    # 6e. 回合执行完 → 组装前端需要的 ActionResponse（与 build_action_response 做的事一致）
                    response = build_action_response(worker_db, session_id, result)

                    # 6f. 发送第二条调试事件 → 前端监视器可见”守秘人回合完成”
                    emit_debug(enqueue_debug, phase="stream", name="action_stream", status="success", message="守秘人回合完成。")

                    # 6g. 把组装好的完整响应放入队列（类型为 “result”）
                    #     model_dump(mode=”json”) 将 Pydantic 模型转为可 JSON 序列化的纯 dict
                    events.put({"type": "result", "response": response.model_dump(mode="json")})

                    # 6h. 标记追踪记录为成功
                    finish_trace_run(trace_recorder, "success")

                    # 6i. 与 submit_action 一样，如果守秘人判定需要生成场景插图
                    if result.get("needs_image"):
                        session = worker_db.get(models.GameSession, session_id)  # 重新获取最新会话（可能被 run_turn 修改过）
                        if session and session.turn_logs:                        # 确保会话存在且有回合记录
                            latest_turn = max(session.turn_logs, key=lambda log: log.turn_index)  # 取最新回合（turn_index 最大）
                            image_gen = ImageGenerator()                        # 图片生成器实例
                            image_url = image_gen.generate_and_save(            # 生成并持久化图片
                                worker_db, latest_turn.id,
                                result["narration"],
                                result.get("image_scene_type", "")
                            )
                            if image_url:
                                # 生成成功 → 以独立事件 put 进队列，前端收到 {type:”image”,...} 后渲染图片
                                events.put({"type": "image", "url": image_url, "turn_id": latest_turn.id, "metadata": latest_turn.image_metadata})
                            else:
                                # 生成失败 → 发送 warning 调试事件（不影响主流程继续）
                                emit_debug(enqueue_debug, phase="stream", name="image_generation", status="warning", message="图片生成失败或配置未启用。")

                except Exception as exc:
                    # 6j. 异常处理：标记追踪为 error、把错误详情 put 进队列让前端展示
                    finish_trace_run(trace_recorder, "error", str(exc))
                    events.put({"type": "error", "detail": str(exc)})

                finally:
                    # 6k. ★ 无论如何都要执行：
                    #     ① 关闭数据库会话（归还连接池，防止泄漏）
                    #     ② 发送 {“type”:”done”} 作为消费者线程退出条件
                    worker_db.close()
                    events.put({"type": "done"})

            # 7. ★ 启动后台线程 —— 生产者线程开始工作
            #    daemon=True 表示守护线程：主线程退出时自动结束，不会阻止进程关闭（适合 Web 请求场景）
            Thread(target=run_agent, daemon=True).start()

            # 8. ★ 消费循环（主线程）—— 阻塞从 Queue 取事件，分类处理
            while True:
                event = events.get()  # Queue.get() 阻塞等待，直到后台线程 put 了数据

                # 8a. done 事件 → 后台线程 finish，跳出循环，结束生成器（→ StreamingResponse 关闭连接）
                if event.get("type") == "done":
                    break

                # 8b. result 事件 → 完整 ActionResponse 已就绪
                if event.get("type") == "result":
                    response_payload = event["response"]

                    # ★ 流式特有效果：模拟打字 —— 把 narration 按中文标点切块，逐块发送
                    #   每块 yield 后 sleep 15ms，前端逐字渲染，体验比一次性弹出全文好得多
                    for chunk in split_stream_text(str(response_payload.get("narration", ""))):
                        yield encode_stream_event({"type": "chunk", "content": chunk})
                        time.sleep(0.015)  # 15ms 延迟模拟打字节奏，太快了看不清，太慢了显得卡

                    # 分块发完后，发送 final 事件包裹完整 response → 前端可以关闭打字动画、更新状态面板
                    yield encode_stream_event({"type": "final", "response": response_payload})
                    continue  # 跳过末尾的通用 encode，继续取下一个事件

                # 8c. image 事件 → 图片生成完成，以独立事件发送（不等 final 一起）
                if event.get("type") == "image":
                    yield encode_stream_event({"type": "image", "url": event["url"], "turnId": event["turn_id"], "metadata": event.get("metadata", {})})
                    continue  # 跳过通用 encode，继续取下一个事件

                # 8d. 其他事件（debug、error 等）→ 原样编码发送
                yield encode_stream_event(event)

        except Exception as exc:
            # 9. 生成器层的异常兜底 —— 如果 event_stream 自身出错（如 JSON 序列化失败、Queue 异常）
            #    发送 error 事件给前端，避免前端一直挂起无响应
            yield encode_stream_event({"type": "error", "detail": str(exc)})

    # 10. 返回 StreamingResponse，关键配置：
    #     - media_type=”application/x-ndjson”：告诉浏览器每行是一个独立 JSON（NDJSON 格式）
    #     - X-Accel-Buffering: no：禁用 Nginx 代理缓冲（否则 Nginx 可能缓存完整响应才转发，破坏流式效果）
    #     - Cache-Control: no-cache：禁止浏览器/CDN 缓存流式响应
    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@router.post("/assistant/chat", response_model=schemas.AssistantChatResponse)
def assistant_chat(payload: schemas.AssistantChatRequest, db: Session = Depends(get_db)) -> dict:
    """处理非流式场外助手问答请求。

    【中文名称】助手对话（非流式）

    【功能说明】
    这是前端“游戏助手”面板对应的普通问答接口。
    它会接收玩家问题和可选的会话 ID，然后调用 `GameAssistantAgent.chat()`，
    返回一次性完整答案，而不是像主游戏回合那样边生成边推送。

    【实现方法】
    1. 如果前端带了 `session_id`，先确认这局游戏确实存在。
    2. 创建一条 trace run，记录这次助手调用，方便 `/monitor` 页面回放。
    3. 把检索增强参数原样传给 `assistant_agent`。
    4. 成功时把 trace 标成 `success`，失败时标成 `error`。

    【为什么要单独记 trace】
    因为助手虽然不参与主回合推进，但同样会触发检索、提示词拼装、LLM 调用。
    把它记进监控系统后，你就能把“游戏主流程”和“场外助手流程”分开观察。
    """
    if payload.session_id and db.get(models.GameSession, payload.session_id) is None:
        raise HTTPException(status_code=404, detail="未找到指定会话")
    trace_recorder = create_trace_run(session_id=payload.session_id, source="assistant", metadata={"stream": False, "message": payload.message})  # trace_recorder = 本次助手请求的监控记录器
    try:
        result = get_assistant_agent().chat(
            db,
            message=payload.message,
            session_id=payload.session_id,
            mode=payload.mode,
            enable_mqe=payload.enable_mqe,
            mqe_expansions=payload.mqe_expansions,
            enable_hyde=payload.enable_hyde,
            top_k=payload.top_k,
            candidate_pool_multiplier=payload.candidate_pool_multiplier,
            trace_recorder=trace_recorder,
        )
        finish_trace_run(trace_recorder, "success")
        return result
    except Exception as exc:
        finish_trace_run(trace_recorder, "error", str(exc))
        raise


@router.post("/assistant/chat/stream")
def assistant_chat_stream(payload: schemas.AssistantChatRequest, db: Session = Depends(get_db)) -> StreamingResponse:
    """处理流式场外助手问答请求。

    【中文名称】助手对话（流式）

    【功能说明】
    这个接口和 `assistant_chat()` 的目标一样，都是回答场外问题，
    但返回方式不同：它会把答案拆成多个 NDJSON 事件流式推给前端。
    前端收到 `chunk` 事件时，会像打字机一样逐步显示回答内容，
    体验上更接近实时聊天。

    【实现方法】
    1. 先验证 `session_id` 是否存在。
    2. 构造内部生成器 `event_stream()`，作为 `StreamingResponse` 的数据源。
    3. 在后台线程中运行真正的助手逻辑，避免阻塞当前响应生成器。
    4. 用线程安全 `Queue` 在线程之间传递 debug 事件、最终结果和错误信息。
    5. 把完整答案切成多个文本块，依次发成 `chunk` 事件。

    【为什么这里要开线程】
    因为 FastAPI 这层要一边“等待助手产出结果”，一边“持续向客户端吐事件”。
    如果直接在当前生成器里同步跑完整个助手流程，就要等答案全生成完才能返回第一段内容，
    那就失去流式显示的意义了。
    """
    if payload.session_id and db.get(models.GameSession, payload.session_id) is None:
        raise HTTPException(status_code=404, detail="未找到指定会话")

    def event_stream() -> Iterator[str]:
        """把助手内部执行过程编码成 NDJSON 事件流。

        事件类型包括：
        - start: 流已建立
        - debug: 调试事件
        - chunk: 一小段回答正文
        - citations: 引用来源
        - final: 完整最终结果
        - error: 错误信息
        """
        try:
            yield encode_stream_event({"type": "start"})  # 通知前端：流已建立
            events = Queue()  # events = 后台线程与当前生成器共享的事件队列

            def enqueue_debug(event: dict) -> None:
                """把助手内部调试事件包装成统一流事件塞进队列。"""
                events.put({"type": "debug", "event": event})

            def run_assistant() -> None:
                """在后台线程里真正执行助手逻辑。

                注意这里会单独创建一个数据库会话，而不是复用外层 `db`。
                原因是外层请求线程和后台线程不能安全共享同一个 SQLAlchemy Session。
                """
                worker_db = SessionLocal()  # worker_db = 后台线程专用数据库会话
                trace_recorder = create_trace_run(session_id=payload.session_id, source="assistant", metadata={"stream": True, "message": payload.message})  # trace_recorder = 流式助手调用的监控记录器
                try:
                    emit_debug(enqueue_debug, phase="stream", name="assistant_stream", status="start", message="助手请求开始。")
                    result = get_assistant_agent().chat(
                        worker_db,
                        message=payload.message,
                        session_id=payload.session_id,
                        mode=payload.mode,
                        enable_mqe=payload.enable_mqe,
                        mqe_expansions=payload.mqe_expansions,
                        enable_hyde=payload.enable_hyde,
                        top_k=payload.top_k,
                        candidate_pool_multiplier=payload.candidate_pool_multiplier,
                        debug_emit=enqueue_debug,
                        trace_recorder=trace_recorder,
                    )
                    emit_debug(enqueue_debug, phase="stream", name="assistant_stream", status="success", message="助手请求完成。")
                    events.put({"type": "result", "response": result})  # result = 完整助手回答，稍后由主线程拆成 chunk
                    finish_trace_run(trace_recorder, "success")
                except Exception as exc:
                    finish_trace_run(trace_recorder, "error", str(exc))
                    events.put({"type": "error", "detail": str(exc)})
                finally:
                    worker_db.close()
                    events.put({"type": "done"})  # done = 告诉主线程“后台任务结束，可以收尾了”

            Thread(target=run_assistant, daemon=True).start()  # 启动后台线程，避免阻塞当前流响应
            while True:
                event = events.get()  # event = 从后台线程取出的下一条流事件
                if event.get("type") == "done":
                    break
                if event.get("type") == "result":
                    result = event["response"]  # result = 完整回答字典，含 answer/citations/spoiler_blocked 等字段
                    for chunk in split_stream_text(result["answer"]):
                        yield encode_stream_event({"type": "chunk", "content": chunk})
                        time.sleep(0.01)
                    yield encode_stream_event({"type": "citations", "citations": result.get("citations", [])})
                    yield encode_stream_event({"type": "final", "response": result})
                    continue
                yield encode_stream_event(event)
        except Exception as exc:
            yield encode_stream_event({"type": "error", "detail": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@router.get("/monitor/settings", response_model=schemas.AgentTraceSettingsOut)
def get_monitor_settings(db: Session = Depends(get_db)) -> dict[str, int]:
    """读取 Agent 监控系统配置。

    【中文名称】获取监控设置

    【功能说明】
    `/monitor` 页面需要知道当前最多保留多少条监控记录，
    这个接口就是把数据库里的监控设置读取出来返回给前端。
    """
    return get_monitor_settings_payload(db)


@router.put("/monitor/settings", response_model=schemas.AgentTraceSettingsOut)
def put_monitor_settings(payload: schemas.AgentTraceSettingsUpdate, db: Session = Depends(get_db)) -> dict[str, int]:
    """更新 Agent 监控系统配置。

    【中文名称】更新监控设置

    【功能说明】
    当前主要用于修改“最多保留多少条 trace record”。
    更新后，后端的 retention 逻辑会按这个上限清理旧记录。
    """
    return update_monitor_settings(db, payload.max_records)


@router.get("/monitor/runs", response_model=list[schemas.AgentTraceRunOut])
def list_monitor_runs(
    session_id: str | None = None,
    source: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[models.AgentTraceRun]:
    """分页查询监控系统中的 run 记录。

    【中文名称】列出监控运行批次

    【功能说明】
    `run` 可以理解为“一次完整执行链”的总记录，
    例如一次玩家行动、一次场外助手问答，都会形成一条 run。
    前端 monitor 页先查 run，再按 run_id 深入看更细的 record。

    【实现方法】
    1. 从 `AgentTraceRun` 表开始建查询。
    2. 按 session_id / source / status 可选过滤。
    3. 按 started_at 倒序，保证最新运行排最前。
    4. 最后做 offset + limit 分页。
    """
    query = db.query(models.AgentTraceRun)  # query = run 级别监控查询对象
    if session_id:
        query = query.filter(models.AgentTraceRun.session_id == session_id)
    if source:
        query = query.filter(models.AgentTraceRun.source == source)
    if status:
        query = query.filter(models.AgentTraceRun.status == status)
    return query.order_by(models.AgentTraceRun.started_at.desc()).offset(offset).limit(limit).all()


@router.get("/monitor/records", response_model=list[schemas.AgentTraceRecordOut])
def list_monitor_records(
    run_id: str | None = None,
    session_id: str | None = None,
    agent_name: str | None = None,
    status: str | None = None,
    source: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[models.AgentTraceRecord]:
    """分页查询监控系统中的细粒度步骤记录。

    【中文名称】列出监控步骤记录

    【功能说明】
    `record` 比 `run` 更细，通常对应一次 Agent 步骤、一个 Tool 调用、
    或一段 commit / reflection / plan 过程。
    学项目时，这个接口特别有用，因为它能帮你回看“某次行动内部到底经历了哪些节点”。
    """
    query = build_monitor_record_query(db, run_id=run_id, session_id=session_id, agent_name=agent_name, status=status, source=source)  # query = record 级别监控查询对象
    return query.order_by(models.AgentTraceRecord.created_at.desc(), models.AgentTraceRecord.sequence.desc()).offset(offset).limit(limit).all()


@router.get("/monitor/events/stream")
def monitor_events_stream() -> StreamingResponse:
    """向前端持续推送实时监控事件。

    【中文名称】实时监控事件流

    【功能说明】
    monitor 页面除了能查历史记录，还能实时看到新产生的 Agent/Tool 事件。
    这个接口会把 `monitor_event_stream()` 产出的事件逐条转成 NDJSON，
    让前端像订阅消息总线一样持续接收。
    """
    def event_stream() -> Iterator[str]:
        """把内部监控事件编码成可被前端逐行解析的流。"""
        for event in monitor_event_stream():
            yield encode_stream_event(event)

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@router.delete("/monitor/records/{record_id}")
def delete_monitor_record(record_id: str, db: Session = Depends(get_db)) -> dict[str, int | str]:
    """删除单条监控步骤记录。

    【中文名称】删除单条监控记录

    【功能说明】
    用于 monitor 页面手动清理某一条具体 record。
    删除后还会顺带调用 `cleanup_empty_runs()`，
    把那些已经没有任何子 record 的空 run 一并清扫掉。
    """
    record = db.get(models.AgentTraceRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="未找到指定监控记录")
    db.delete(record)
    db.commit()
    cleanup_empty_runs(db)
    return {"status": "已删除", "deleted": 1}


@router.delete("/monitor/runs/{run_id}")
def delete_monitor_run(run_id: str, db: Session = Depends(get_db)) -> dict[str, int | str]:
    """删除一整条监控运行批次。

    【中文名称】删除监控运行批次

    【功能说明】
    run 是监控系统里的“父记录”。
    删除 run 通常意味着整次执行链都不再保留。
    这里会顺便统计该 run 下面有多少条 record，返回给前端用于提示删除规模。
    """
    run = db.get(models.AgentTraceRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="未找到指定运行记录")
    record_count = db.query(models.AgentTraceRecord).filter(models.AgentTraceRecord.run_id == run_id).count()
    db.delete(run)
    db.commit()
    return {"status": "已删除", "deleted_runs": 1, "deleted_records": record_count}


@router.delete("/monitor/records")
def delete_monitor_records(
    run_id: str | None = None,
    session_id: str | None = None,
    agent_name: str | None = None,
    status: str | None = None,
    source: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    """按过滤条件批量删除监控步骤记录。

    【中文名称】批量删除监控记录

    【功能说明】
    当前端想清空某个 session、某个 agent、某个状态下的 record 时，
    不需要一条条删，而是通过这个接口批量完成。
    删除完成后同样会清理已经空掉的 run。
    """
    query = build_monitor_record_query(db, run_id=run_id, session_id=session_id, agent_name=agent_name, status=status, source=source)  # query = 已带过滤条件的批量删除查询
    deleted = query.delete(synchronize_session=False)
    db.commit()
    cleanup_empty_runs(db)
    return {"status": "已删除", "deleted": deleted}


def build_monitor_record_query(
    db: Session,
    *,
    run_id: str | None = None,
    session_id: str | None = None,
    agent_name: str | None = None,
    status: str | None = None,
    source: str | None = None,
):
    """构造带筛选条件的监控 record 查询对象。

    【中文名称】构建监控记录查询

    【功能说明】
    `list_monitor_records()` 和 `delete_monitor_records()` 都要按相同规则过滤 record。
    为了避免把同样的查询拼接逻辑复制两遍，这里提取成一个公共函数。

    【实现方法】
    1. 从 `AgentTraceRecord` 表开始查询。
    2. 按 run_id / session_id / agent_name / status / source 逐项追加 filter。
    3. 返回未执行的 query，交给调用方继续做排序、分页或删除。

    【为什么返回 query 而不是 list】
    因为不同调用方后续动作不同：
    - 列表接口还要 `order_by + offset + limit + all`
    - 删除接口则要直接 `delete`
    返回 query 能保留这种灵活性。
    """
    query = db.query(models.AgentTraceRecord)  # query = AgentTraceRecord 的基础查询对象
    if run_id:
        query = query.filter(models.AgentTraceRecord.run_id == run_id)
    if session_id:
        query = query.filter(models.AgentTraceRecord.session_id == session_id)
    if agent_name:
        query = query.filter(models.AgentTraceRecord.agent_name == agent_name)
    if status:
        query = query.filter(models.AgentTraceRecord.status == status)
    if source:
        query = query.filter(models.AgentTraceRecord.source == source)
    return query


def _scene_type_to_aspect_ratio(scene_type: str) -> str:
    """把图片场景类型转换成前端展示使用的宽高比。

    【中文名称】场景类型转宽高比

    【功能说明】
    当前图片生成逻辑里，不同场景类型可能适合不同画幅：
    - `new_scene` 更适合横构图，所以返回 `16:9`
    - 其他情况默认使用更通用的 `1:1`

    这个函数虽然很小，但它把“图片生成内部语义”隔离成了“前端可直接消费的展示参数”。
    """
    return "16:9" if scene_type == "new_scene" else "1:1"


def build_action_response(db: Session, session_id: str, result: dict) -> schemas.ActionResponse:
    # Agent 的内部状态较大，这里只整理前端需要展示和持久化的公开字段。
    # 可以把它理解成“后端 ViewModel 组装层”：
    # - Supervisor 返回的是偏内部的运行结果
    # - 前端真正需要的是 ActionResponse 这个稳定结构
    # 学习前后端对接时，建议把这里和 frontend/src/types.ts 里的 ActionResponse 对照着看。
    # 1. 重新查询最新会话视图；Supervisor 已经落库，所以这里拿到的是更新后的 session/clues/items/turns。
    session_out = build_session_out(db, session_id)  # session_out = 前端需要展示的聚合会话对象
    # 2. 构造 Pydantic 响应模型；response_model 会确保字段结构和 frontend/src/types.ts 对齐。
    return schemas.ActionResponse(  # ActionResponse = 一次玩家行动最终返回给前端的稳定结构
        session=session_out,  # session = 更新后的会话视图，包含角色、线索、物品、最近回合
        narration=result.get("narration", ""),  # narration = 守秘人最终叙事文本，缺失时给空字符串
        options=result.get("options", []),  # options = 下一步行动建议列表，缺失时给空列表
        dice_results=result.get("dice_results", []),  # dice_results = 骰点结果列表，用于前端展示检定细节
        skill_checks=result.get("skill_checks", []),  # skill_checks = 技能检定结果列表
        sanity_checks=result.get("sanity_checks", []),  # sanity_checks = 理智检定结果列表
        discovered_clues=[  # discovered_clues = 本回合发现的新线索，转换为 API 输出模型
            schemas.ClueOut.model_validate(clue)  # Clue ORM/Pydantic 对象 -> ClueOut
            for clue in result.get("discovered_clues", [])  # 遍历 Supervisor 返回的线索对象列表
        ],
        state_delta=result.get("state_delta", {}),  # state_delta = 本回合结构化状态变化，方便前端调试展示
        needs_clarification=bool(result.get("needs_clarification")),  # needs_clarification = 是否需要玩家进一步澄清
        needs_image=bool(result.get("needs_image")),  # needs_image = 是否需要异步生成场景图片
        image_aspect_ratio=_scene_type_to_aspect_ratio(result.get("image_scene_type", "")),  # image_aspect_ratio = 根据场景类型转换出的图片宽高比
        image_url=result.get("image_url"),  # image_url = 已生成图片 URL；流式接口可能稍后再发 image 事件
        image_metadata=result.get("image_metadata", {}),  # image_metadata = 图片生成参数和附加信息
    )


def encode_stream_event(payload: dict) -> str:
    # 使用 NDJSON：一行一个 JSON 事件，便于浏览器流式解析。
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def split_stream_text(text: str) -> Iterator[str]:
    # 优先在中文标点处断句；长句则按固定长度切块，避免前端等待过久。
    buffer = ""
    for char in text:
        buffer += char
        if len(buffer) >= 12 or char in "。！？；\n":
            yield buffer
            buffer = ""
    if buffer:
        yield buffer


def build_session_out(db: Session, session_id: str) -> schemas.SessionOut:
    # 会话输出是前端页面的聚合视图，包含角色、线索、物品和最近回合。
    session = (
        db.query(models.GameSession)
        .options(
            selectinload(models.GameSession.character),
            selectinload(models.GameSession.clues),
            selectinload(models.GameSession.inventory_items),
            selectinload(models.GameSession.flags),
            selectinload(models.GameSession.turn_logs),
        )
        .filter(models.GameSession.id == session_id)
        .one_or_none()
    )
    if session is None:
        raise HTTPException(status_code=404, detail="未找到指定会话")
    recent_turns = sorted(session.turn_logs, key=lambda item: item.turn_index)[-10:]
    return schemas.SessionOut(
        id=session.id,
        scenario_id=session.scenario_id,
        character_id=session.character_id,
        title=session.title,
        current_location=session.current_location,
        current_scene=session.current_scene,
        current_time=session.current_time,
        story_phase=session.story_phase,
        danger_level=session.danger_level,
        summary=session.summary,
        state=session.state,
        created_at=session.created_at,
        updated_at=session.updated_at,
        character=schemas.CharacterOut.model_validate(session.character),
        clues=[schemas.ClueOut.model_validate(clue) for clue in session.clues],
        inventory_items=[schemas.InventoryItemOut.model_validate(item) for item in session.inventory_items],
        flags=[schemas.StoryFlagOut.model_validate(flag) for flag in session.flags],
        recent_turns=[schemas.TurnLogOut.model_validate(turn) for turn in recent_turns],
    )
