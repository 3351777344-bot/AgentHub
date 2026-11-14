import { ApiError, request } from "./api.js";

// Parse complete lines across transport chunks. CRLF may itself cross a chunk boundary.
export async function* decodeEvents(body) {
  let buffer = "";
  let event = "message";
  let id = "";
  let data = [];
  const chunks = body.pipeThrough(new TextDecoderStream());
  for await (const chunk of chunks) {
    buffer += chunk;
    for (;;) {
      const end = buffer.search(/[\r\n]/);
      if (end < 0 || (buffer[end] === "\r" && end === buffer.length - 1)) break;
      const line = buffer.slice(0, end);
      buffer = buffer.slice(end + (buffer.slice(end, end + 2) === "\r\n" ? 2 : 1));
      if (!line) {
        if (data.length) yield { event, id, data: JSON.parse(data.join("\n")) };
        event = "message";
        data = [];
        continue;
      }
      if (line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon < 0 ? line : line.slice(0, colon);
      const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "event") event = value;
      if (field === "id" && !value.includes("\0")) id = value;
      if (field === "data") data.push(value);
    }
  }
}

export async function* events(path, options) {
  const response = await request(path, options);
  if (!response.body || !response.headers.get("Content-Type")?.startsWith("text/event-stream")) {
    throw new ApiError(0, { code: "stream_incomplete" });
  }
  const requestId = response.headers.get("X-Request-ID");
  for await (const frame of decodeEvents(response.body)) {
    yield { ...frame, requestId };
  }
}
