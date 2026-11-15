type LoginPreferences = { remember: boolean; account: string }
type CurrentUser = { id: string; username: string; role: 'admin' | 'employee'; workspace_id: string; permissions: string[] }
type Session = { user: CurrentUser; expires_at: number; remember: boolean }
type AuthResult = { ok: true; data: Session | null } | { ok: false; message: string }
type ServiceResult<T> = { ok: true; data: T } | { ok: false; message: string }
type AiProvider = { id: string; name: string; base_url: string; model: string; max_tokens: number | null; enabled: boolean; has_api_key: boolean; created_at: number; updated_at: number }
type AiSettings = { providers: AiProvider[]; default_provider_id: string | null; global_prompt: string }
type ToolEvent = { id: string; call_id: string; name: string; command: string; status: 'success' | 'denied' | 'error' | 'cancelled'; duration_ms: number; result: { partial?: boolean; shops?: unknown[]; ok?: boolean; code?: number; msg?: string; version?: string; category?: string; total?: number; next_page?: number; data?: unknown; path?: string; download_path?: string; media_type?: string; file_id?: string; filename?: string; size?: number; sheets?: { name: string; rows: number }[]; error?: { code: string; message: string } } }
type ChatMessage = { id: string; role: 'user' | 'assistant'; content: string; created_at: number; reasoning_content?: string; interrupted?: boolean; thinking?: boolean; reasoning_effort?: 'low' | 'high' | 'max'; tool_events?: ToolEvent[] }
type Conversation = { id: string; title: string; user_id: string; workspace_id: string; created_at: number; updated_at: number; model: string; provider_name: string; messages: ChatMessage[] }
type ConversationSummary = Pick<Conversation, 'id' | 'title' | 'created_at' | 'updated_at' | 'model'>

interface Window {
  desktop?: {
    clipboard: {
      writeText(value: string): Promise<void>
    }
    service: {
      request<T>(action: string, value?: unknown): Promise<ServiceResult<T>>
    }
    auth: {
      current(): Promise<AuthResult>
      login(value: { username: string; password: string; remember: boolean }): Promise<AuthResult>
      logout(): Promise<AuthResult>
    }
    preferences: {
      read(): Promise<LoginPreferences>
      write(value: LoginPreferences): Promise<void>
    }
    minimize(): void
    maximize(): void
    close(): void
    openExternal(url: string): Promise<void>
  }
}
