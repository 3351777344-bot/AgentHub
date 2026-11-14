import { api, list, download, setSession } from "./api.js";
import { h, empty, explanation } from "./ui.js";
import { home, users, logs } from "./management.js";
import { knowledge } from "./knowledge.js";
import { chat } from "./chat.js";
import { agents } from "./agents.js";
import { tools } from "./tools.js";
import { evaluations } from "./evaluations.js";

const pages = {
  home: ["工作台", "", home],
  chat: ["对话", "在私有会话中聊天，或选择一个知识库查看回答与引用。", chat],
  knowledge: ["知识库", "上传 TXT、Markdown 或文本 PDF，并跟踪后台入库任务。", knowledge],
  agents: ["Agent", "编辑配置、发起运行，并查看执行步骤与审批。", agents],
  tools: ["工具与 MCP", "查看角色可用的工具及版本。管理员可以维护 MCP 服务与注册。", tools],
  evaluations: ["评测", "维护版本化样例，运行评测并比较相同数据集下的配置。", evaluations],
  logs: ["运行日志", "按资源查看工具调用、入库、Agent 和评测记录。", logs],
  users: ["用户管理", "创建账号、分配角色并管理启用状态。", users, true],
};

const $ = (id) => document.getElementById(id);
const loginForm = $("login-form");
const view = $("view");
const noticeNode = $("notice");
let user = null;
let scope = new AbortController();

function notice(message, error = false) {
  noticeNode.hidden = !message;
  noticeNode.textContent = message ?? "";
  noticeNode.className = error ? "error" : "";
  noticeNode.setAttribute("role", error ? "alert" : "status");
}

function endSession(message) {
  scope.abort();
  setSession(null);
  user = null;
  history.replaceState(null, "", location.pathname);
  $("layout").classList.remove("signed-in");
  $("sidebar").hidden = $("account").hidden = view.hidden = true;
  $("nav").replaceChildren();
  $("identity").textContent = "";
  view.replaceChildren();
  $("login-panel").hidden = false;
  loginForm.reset();
  loginForm.elements.username.focus();
  notice(message);
}

function route() {
  try {
    const parts = location.hash.startsWith("#/") ? location.hash.slice(2).split("/") : [];
    return parts.length ? parts.map(decodeURIComponent) : ["home"];
  } catch {
    return ["unknown"];
  }
}

function go(page, ...parts) {
  const hash = "#/" + [page, ...parts].map(encodeURIComponent).join("/");
  if (location.hash !== hash) location.hash = hash;
  else render();
}

async function render() {
  if (!user) return;
  scope.abort();
  scope = new AbortController();
  const { signal } = scope;
  const [page, ...parts] = route();
  const current = Object.hasOwn(pages, page) ? pages[page] : null;
  const title = current?.[0] ?? "页面不可用";
  const header = h("h1", { tabindex: "-1" }, title);
  $("nav").replaceChildren(...Object.entries(pages)
    .filter(([, value]) => !value[3] || user.role_name === "admin")
    .map(([key, value]) => h("a", { href: "#/" + key, "aria-current": key === page ? "page" : null }, value[0])));
  notice();
  const content = h("div");
  view.replaceChildren(h("div", { class: "intro" }, header, current?.[1] && h("p", {}, current[1])), content);
  header.focus({ preventScroll: true });
  if (!current || (current[3] && user.role_name !== "admin")) {
    content.append(explanation(current ? "此页面需要管理员权限。" : "请选择控制台导航中的页面。", true));
    return;
  }
  const ctx = {
    user, parts, signal, go, reload: render, notice: (...args) => !signal.aborted && notice(...args),
    api: (path, opts) => api(path, { signal, ...opts }),
    list: (path) => list(path, { signal }),
    download: (path, name) => download(path, name, signal),
  };
  content.setAttribute("aria-busy", "true");
  const loading = empty("正在加载…");
  loading.setAttribute("role", "status");
  content.append(loading);
  try {
    const panel = await current[2](ctx);
    if (!signal.aborted) content.replaceChildren(panel);
  } catch (error) {
    if (!signal.aborted) {
      const retry = h("button", { type: "button" }, "重新加载页面");
      retry.addEventListener("click", render);
      content.replaceChildren(explanation(error.message, true), retry);
    }
  } finally {
    content.removeAttribute("aria-busy");
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = loginForm.querySelector("button");
  if (submit.disabled) return;
  submit.disabled = true;
  notice("正在登录…");
  const data = Object.fromEntries(new FormData(loginForm));
  loginForm.elements.password.value = "";
  try {
    const result = await api("/auth/login", { method: "POST", data, publicCall: true });
    setSession(result.access_token, () => endSession("登录已失效，请重新登录。"));
    try {
      user = await api("/auth/me");
    } catch (error) {
      await api("/auth/logout", { method: "POST" }).catch(() => {});
      throw error;
    }
    $("identity").textContent = user.username + (user.role_name === "admin" ? " · 管理员" : " · 成员");
    $("login-panel").hidden = true;
    $("sidebar").hidden = $("account").hidden = view.hidden = false;
    $("layout").classList.add("signed-in");
    render();
  } catch (error) {
    setSession(null);
    notice(error.message, true);
    loginForm.elements.password.focus();
  } finally {
    submit.disabled = false;
  }
});

$("logout").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await api("/auth/logout", { method: "POST" });
    endSession("已退出登录。");
  } catch (error) {
    if (user) notice(error.message + " 请在连接恢复后重试退出。", true);
  } finally {
    button.disabled = false;
  }
});
window.addEventListener("hashchange", () => {
  // The skip link moves focus without changing the active console page.
  if (location.hash !== "#main") render();
});
document.querySelector(".skip").addEventListener("click", (event) => {
  event.preventDefault();
  $("main").focus();
});
