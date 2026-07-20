export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { cache: "no-store", ...options, headers });
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try {
      const payload = (await response.json()) as {
        detail?: string | Array<{ msg?: string; loc?: Array<string | number> }>;
      };
      if (typeof payload.detail === "string") detail = payload.detail;
      else if (Array.isArray(payload.detail)) {
        detail = payload.detail.map(item => item.msg || "配置值无效").join("；") || detail;
      }
    } catch { /* response is not JSON */ }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
