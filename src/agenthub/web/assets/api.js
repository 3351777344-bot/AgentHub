const prefix = "/api/v1";
let token = null;
let onExpired = () => {};

export function setSession(value, expired = () => {}) {
  token = value;
  onExpired = expired;
}

const messages = {
  invalid_credentials: "用户名或密码有误。",
  invalid_session: "登录已失效，请重新登录。",
  forbidden: "当前账号没有执行此操作的权限。",
  database_unavailable: "数据库暂不可用。恢复后请刷新。",
  validation_error: "输入不符合接口要求，请检查字段值。",
  document_size_limit: "文件超过部署允许的大小。",
  model_not_configured: "请由部署管理员配置模型服务。",
  network_error: "连接失败。请检查网络，重新读取后台状态。",
  stream_incomplete: "连接中断，未收到生成结束事件。请先检查历史。",
  agent_run_managed_by_evaluation: "此运行由评测管理，请在评测页取消对应批次。",
  agent_run_state_conflict: "运行状态已变化，请刷新后再操作。",
  agent_approval_conflict: "审批已变化，请重新读取待审批内容。",
  mcp_definition_changed: "工具定义已变化，请重新发现并复核。",
};

export class ApiError extends Error {
  constructor(status, detail = {}) {
    const { code = "request_failed", request_id, message } = detail;
    const description = messages[code] ?? ({
      401: "认证失败。",
      403: "当前账号没有执行此操作的权限。",
      404: "资源不存在、已删除或当前账号无权访问。",
      409: "当前后台状态不允许此操作，请刷新后检查。",
    }[status] ?? message ?? "请求失败");
    super([description, [status, code].filter(Boolean).join(" / "),
      request_id && "请求 ID " + request_id].filter(Boolean).join(" · "));
    this.status = status;
    this.code = code;
  }
}

export async function request(path, { method = "GET", data, body, signal, headers = {}, publicCall = false } = {}) {
  const session = token;
  const expire = onExpired;
  const outgoing = new Headers(headers);
  if (session && !publicCall) outgoing.set("Authorization", "Bearer " + session);
  if (data !== undefined) {
    outgoing.set("Content-Type", "application/json");
    body = JSON.stringify(data);
  }
  let response;
  try {
    response = await fetch(prefix + path, {
      method, body, signal, headers: outgoing, cache: "no-store", credentials: "omit", redirect: "error",
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ApiError(0, { code: "network_error" });
  }
  if (response.ok) return response;
  const payload = await response.json().catch(() => null);
  if (response.status === 401 && session && token === session && !publicCall) expire();
  throw new ApiError(response.status, {
    ...payload?.error, request_id: payload?.error?.request_id ?? response.headers.get("X-Request-ID"),
  });
}

export async function api(path, options) {
  const response = await request(path, options);
  return response.status === 204 ? null : response.json();
}

export async function list(path, options) {
  const rows = [];
  const separator = path.includes("?") ? "&" : "?";
  for (let offset = 0; offset <= 100000; offset += 100) {
    const page = await api(path + separator + "limit=100&offset=" + offset, options);
    rows.push(...page);
    if (page.length < 100) return rows;
  }
  return rows;
}

export async function download(path, filename, signal) {
  const response = await request(path, { signal });
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function segment(value) {
  return encodeURIComponent(value);
}

export function delay(ms, signal) {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const stop = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", stop);
      resolve();
    }, ms);
    signal.addEventListener("abort", stop, { once: true });
  });
}
