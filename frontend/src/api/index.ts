// API common utilities

const API_BASE = ''

export { API_BASE }

export async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    let msg = `API request failed: ${res.status}`
    try { const j = JSON.parse(text); if (j.detail) msg = j.detail } catch {}
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}
