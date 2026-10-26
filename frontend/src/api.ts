export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch("/api" + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Sentinel-Client": "workspace",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new ApiError(
      "The local service could not be reached. Your inputs are preserved. Check the server and retry.",
      0,
    );
  }
  const result = await response.json();
  if (!response.ok)
    throw new ApiError(
      typeof result.detail === "string"
        ? result.detail
        : "The request could not be completed.",
      response.status,
    );
  return result as T;
}
