// web/src/api.ts — 带访问令牌的 fetch helper（安全鉴权）
// auth_token 配置非空时后端要求 X-Auth-Token 头；401 时询问一次并存 localStorage。
const TOKEN_KEY = 'devforge_token'
let token = localStorage.getItem(TOKEN_KEY) ?? ''

export function getToken(): string {
  return token
}

/** 401 时询问令牌（一次），返回是否成功 */
export function promptToken(): boolean {
  const input = window.prompt('此 DevForge 实例需要访问令牌（configs/default.json 的 auth_token）:')
  if (input && input.trim()) {
    token = input.trim()
    localStorage.setItem(TOKEN_KEY, token)
    return true
  }
  return false
}

/** 带令牌的 fetch；401 时询问令牌并重试一次 */
export async function apiFetch(url: string, opts: RequestInit = {}): Promise<Response> {
  const headers: Record<string, string> = {
    ...(opts.headers as Record<string, string> | undefined),
  }
  if (token) headers['X-Auth-Token'] = token
  let r = await fetch(url, { ...opts, headers })
  if (r.status === 401 && promptToken()) {
    headers['X-Auth-Token'] = token
    r = await fetch(url, { ...opts, headers })
  }
  return r
}

/** WebSocket URL：附加 token 查询参数（ws 握手校验） */
export function wsUrl(path: string): string {
  const p = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return token ? `${p}//${location.host}${path}?token=${encodeURIComponent(token)}` : `${p}//${location.host}${path}`
}
