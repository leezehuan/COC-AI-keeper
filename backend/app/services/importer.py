from pathlib import Path

from sqlalchemy.orm import Session

from app import models
from app.config import get_settings
from app.services.characters import import_characters
from app.services.chunking import chunk_markdown
from app.services.content_index import build_structured_indexes
from app.services.retrieval import RetrievalService, existing_source_paths
from app.utils import resolve_project_path


SCENARIO_NAME = "无光的灯塔"


def import_default_content(db: Session, reset_chroma: bool = False, include_characters: bool = True) -> dict:
    settings = get_settings()
    scenario_path = resolve_project_path(settings.scenario_path)
    rulebook_paths = [resolve_project_path(path) for path in settings.rulebook_path_list]
    character_dir = resolve_project_path(settings.character_dir)

    scenario = db.query(models.Scenario).filter(models.Scenario.name == SCENARIO_NAME).one_or_none()
    if scenario is None:
        scenario = models.Scenario(name=SCENARIO_NAME, source_path=str(scenario_path), metadata_={"era": "1926", "location": "航标岛"})
        db.add(scenario)
        db.commit()
        db.refresh(scenario)

    retrieval = RetrievalService()
    if reset_chroma:
        retrieval.reset()

    scenario_chunks = []
    scenario_entities = []
    clue_index = []
    if scenario_path.exists():
        scenario_chunks = chunk_markdown(scenario_path, "scenario")
        retrieval.upsert_chunks("scenario_chunks", scenario_chunks)
        structured_indexes = build_structured_indexes(scenario_path)
        scenario_entities = structured_indexes["scenario_entities"]
        clue_index = structured_indexes["clue_index"]
        if scenario_entities:
            retrieval.upsert_chunks("scenario_entities", scenario_entities)
        if clue_index:
            retrieval.upsert_chunks("clue_index", clue_index)

    rule_chunks = []
    for path in existing_source_paths(rulebook_paths):
        rule_chunks.extend(chunk_markdown(path, "rule"))
    if rule_chunks:
        retrieval.upsert_chunks("rule_chunks", rule_chunks)

    character_count = import_characters(db, scenario, character_dir) if include_characters else 0
    return {
        "scenario_id": scenario.id,
        "scenario_chunks": len(scenario_chunks),
        "rule_chunks": len(rule_chunks),
        "scenario_entities": len(scenario_entities),
        "clue_index": len(clue_index),
        "characters": character_count,
    }


def ensure_default_scenario(db: Session) -> models.Scenario:
    scenario = db.query(models.Scenario).filter(models.Scenario.name == SCENARIO_NAME).one_or_none()
    if scenario is not None:
        return scenario
    result = import_default_content(db, reset_chroma=False, include_characters=True)
    return db.get(models.Scenario, result["scenario_id"])
