import { delay } from "./api.js";

export function h(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (value === undefined || value === null || value === false) continue;
    node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child !== undefined && child !== null && child !== false) node.append(child);
  }
  return node;
}

export const empty = (text) => h("p", { class: "empty" }, text);
export const stack = (...children) => h("div", { class: "stack" }, children);
export const row = (...children) => h("div", { class: "row" }, children);
export const card = (title, ...children) => h("section", { class: "card stack" }, h("h2", {}, title), children);
export const split = (left, right) => h("div", { class: "split" }, left, right);
export const details = (title, ...children) => h("details", {}, h("summary", {}, title), children);
export const json = (value) => h("pre", {}, JSON.stringify(value, null, 2));
export const records = (items, render, fallback) => items.length
  ? h("div", { class: "list" }, items.map((item) => h("article", { class: "record" }, render(item))))
  : empty(fallback);
export const timestamp = (value) => value ? new Date(value).toLocaleString("zh-CN") : "—";
export const info = (items) => h("dl", {}, items.map(([key, value]) => [h("dt", {}, key), h("dd", {}, value ?? "—")]));
export const link = (label, page, ...parts) => h("a", { href: "#/" + [page, ...parts].map(encodeURIComponent).join("/") }, label);
export const explanation = (text, error = false) => h("p", { class: "note" + (error ? " error" : "") }, text);

const statuses = {
  queued: "排队中", pending: "待处理", running: "运行中", processing: "处理中",
  ready: "可用", completed: "已完成", failed: "失败", cancelled: "已取消",
  waiting_approval: "待审批", paused: "已暂停", passed: "通过", skipped: "已跳过",
  rejected: "已拒绝", started: "已开始", enabled: "已启用", disabled: "已停用",
  succeeded: "成功", uncertain: "结果待核对", interrupted: "已中断",
};
export function status(value) {
  return h("span", { class: "status " + (value ?? "") }, statuses[value] ?? value ?? "未知");
}

let fieldId = 0;

export function field(label, name, props = {}) {
  const { tag = "input", options, value, ...attributes } = props;
  const id = "control-" + ++fieldId;
  const control = h(tag, { name, id, ...attributes }, options?.map(([key, text]) => h("option", { value: key }, text)));
  if (attributes.multiple) {
    for (const option of control.options) option.selected = value?.includes(option.value) ?? false;
  } else if (value !== undefined && value !== null) {
    control.value = value;
  }
  if (attributes.type === "checkbox") return h("label", { class: "check" }, control, label);
  return h("div", { class: "field" }, h("label", { for: id }, label), control);
}

export const select = (label, name, options, attrs = {}) => field(label, name, { ...attrs, tag: "select", options });
export const area = (label, name, attrs = {}) => field(label, name, { ...attrs, tag: "textarea" });
export const number = (label, name, value, min, max) => field(label, name, { type: "number", value, min, max, required: value != null });
export const optionRows = (rows, label = (item) => item.name) => rows.map((item) => [item.id, label(item)]);
export const multi = (form, key) => new FormData(form).getAll(key);
export const optionalNumber = (data, key) => data[key] === "" ? null : Number(data[key]);
export const table = (headers, rows) => h("div", { class: "table-wrap" }, h("table", {},
  h("thead", {}, h("tr", {}, headers.map((header) => h("th", { scope: "col" }, header)))),
  h("tbody", {}, rows.map((item) => h("tr", {}, item.map((cell) => h("td", {}, cell)))))));

function failure(ctx, error) {
  if (!ctx.signal.aborted && error?.name !== "AbortError") ctx.notice(error.message, true);
}

export function button(label, ctx, action, props = {}) {
  const element = h("button", { type: "button", ...props }, label);
  element.addEventListener("click", async () => {
    element.disabled = true;
    try {
      ctx.notice();
      await action(element);
    } catch (error) {
      failure(ctx, error);
    } finally {
      element.disabled = false;
    }
  });
  return element;
}

export function form(ctx, label, fields, action, { extra = [], ...attrs } = {}) {
  const submit = h("button", { type: "submit", class: "primary" }, label);
  const progress = h("span", { role: "status" });
  const element = h("form", { method: "post", ...attrs }, fields, row(submit, extra, progress));
  element.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    submit.disabled = true;
    progress.textContent = "正在提交…";
    try {
      ctx.notice();
      await action(Object.fromEntries(new FormData(element)), element);
    } catch (error) {
      failure(ctx, error);
    } finally {
      submit.disabled = false;
      progress.textContent = "";
    }
  });
  return element;
}

export function localPanel(ctx, holder, load) {
  let revision = 0;
  return async () => {
    const current = ++revision;
    holder.setAttribute("aria-busy", "true");
    if (!holder.childElementCount) holder.append(empty("正在加载…"));
    try {
      const contents = await load();
      if (current === revision && !ctx.signal.aborted) holder.replaceChildren(contents);
    } catch (error) {
      if (current === revision && !ctx.signal.aborted) {
        holder.replaceChildren(explanation(error.message, true));
      }
    } finally {
      if (current === revision) holder.removeAttribute("aria-busy");
    }
  };
}

export async function watch(ctx, refresh, ms = 2000) {
  try {
    while (await refresh()) await delay(ms, ctx.signal);
  } catch (error) {
    failure(ctx, error);
  }
}
