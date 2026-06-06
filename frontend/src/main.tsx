// 【前端入口文件】
// Vite 构建入口，挂载 React 应用到 HTML 的 #root 元素。
// React.StrictMode 在开发模式下会额外渲染一次组件以检测副作用。
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
