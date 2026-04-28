// SSE 流式连接通用逻辑
// 用于智能对话和智能助手的流式输出

export interface SSECallbacks {
  onToken?: (token: string) => void
  onToolStart?: (tool: string, agent?: string) => void
  onToolEnd?: (tool: string, result: string, agent?: string) => void
  onDelegate?: (agent: string, task: string) => void
  onDelegateDone?: (preview: string) => void
  onPlan?: (todos: any[]) => void
  onDone?: (data: any) => void
  onError?: (message: string) => void
  onAgentStart?: (agent: string, task: string) => void
  onInterrupt?: (tool: string, input: any, message: string) => void
}

export async function connectSSE(
  url: string,
  body: any,
  callbacks: SSECallbacks,
  timeoutMs: number = 120000,
): Promise<void> {
  const controller = new AbortController()
  const timeoutHandle = setTimeout(() => controller.abort(), timeoutMs)

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    })

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
      callbacks.onError?.(err.detail || String(res.status))
      return
    }

    const reader = res.body?.getReader()
    if (!reader) {
      callbacks.onError?.('无法建立流式连接')
      return
    }

    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''

      for (const part of parts) {
        if (!part.trim()) continue

        let eventType = ''
        let dataLines: string[] = []

        for (const line of part.split('\n')) {
          if (line.startsWith('event:')) {
            eventType = line.slice(6).trim()
          } else if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).trimStart())
          }
        }

        const dataStr = dataLines.join('\n')
        if (!dataStr) continue

        // eventType available for future use
        void eventType

        try {
          const data = JSON.parse(dataStr)
          handleEvent(data, callbacks)
        } catch {
          // ignore parse errors
        }
      }
    }
  } catch (e: any) {
    if (e.name === 'AbortError') {
      callbacks.onError?.('响应超时')
    } else {
      callbacks.onError?.(e.message || '连接错误')
    }
  } finally {
    clearTimeout(timeoutHandle)
  }
}

function handleEvent(event: any, cb: SSECallbacks) {
  const type = event.type

  switch (type) {
    case 'token':
      cb.onToken?.(event.content || '')
      break
    case 'tool_start':
      cb.onToolStart?.(event.tool || '?', event.agent)
      break
    case 'tool_end':
      cb.onToolEnd?.(event.tool || '?', event.result || '', event.agent)
      break
    case 'agent_start':
      cb.onAgentStart?.(event.agent || '?', event.task || '')
      break
    case 'delegate':
      cb.onDelegate?.(event.agent || '?', event.task || '')
      break
    case 'delegate_done':
      cb.onDelegateDone?.(event.preview || '')
      break
    case 'plan':
      cb.onPlan?.(event.todos || [])
      break
    case 'done':
      cb.onDone?.(event)
      break
    case 'error':
      cb.onError?.(event.message || '未知错误')
      break
    case 'interrupt':
      cb.onInterrupt?.(event.tool || '?', event.input || {}, event.message || '')
      break
  }
}
