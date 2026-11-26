type TokenUsage = { input_tokens: number; output_tokens: number; total_tokens: number; cache_hit_tokens: number; cache_miss_tokens: number; requests: number; unknown_requests: number; failed_requests: number; complete?: boolean }
type ClarificationRequest = { kind?: 'question'; id: string; questions: { id: string; title: string; options: { id: string; label: string; description?: string }[] }[] }
type VerificationRequest = { kind: 'verification'; id: string; channel: string; purpose: string; destination: string; expires_at_ms: number; server_now_ms: number }
type DepartmentRecord = { id: string | null; name: string; employee_count: number }
type PersonalProfile = { name: string; account: string; avatar: string | null; department: string; job_title: string; phone: string; employment_status: 'active' | 'resigned' }
type EmployeeRecord = { id: string; user_id: string; name: string; account: string; avatar: string | null; department: string; job_title: string; phone: string; employment_status: 'active' | 'resigned'; linked_accounts: {platform: string; account: string}[] }
type DesktopRelease = { version: string; filename: string; size: number; sha256: string; notes: string; published_at: string; download_path: string }
type UpdateStatus = { phase: 'idle' | 'checking' | 'available' | 'downloading' | 'verifying' | 'installing' | 'current' | 'error'; current_version: string; release: DesktopRelease | null; percent: number; downloaded?: number; speed_bytes_per_second?: number; remaining_seconds?: number | null; message: string }
type LoginPreferences = { remember: boolean; account: string }
type CurrentUser = { name?: string; avatar?: string | null; id: string; username: string; role: 'admin' | 'employee'; workspace_id: string; permissions: string[] }
type Session = { user: CurrentUser; expires_at: number; remember: boolean }
type AuthResult = { ok: true; data: Session | null } | { ok: false; message: string }
type ServiceResult<T> = { ok: true; data: T } | { ok: false; message: string }
type AiProvider = { id: string; name: string; base_url: string; model: string; max_tokens: number | null; enabled: boolean; has_api_key: boolean; created_at: number; updated_at: number }
type AiSettings = { providers: AiProvider[]; default_provider_id: string | null; global_prompt: string }
type ToolEvent = { id: string; call_id: string; name: string; command: string; status: 'success' | 'denied' | 'error' | 'cancelled'; duration_ms: number; result: { partial?: boolean; shops?: unknown[]; ok?: boolean; code?: number; msg?: string; version?: string; category?: string; total?: number; next_page?: number; data?: unknown; path?: string; download_path?: string; media_type?: string; file_id?: string; filename?: string; size?: number; sheets?: { name: string; rows: number }[]; error?: { code: string; message: string } } }
type ChatAttachment = { id: string; filename: string; size: number; media_type: string; path: string }
type ChatMessage = { attached_files?: ChatAttachment[]; usage?: TokenUsage; id: string; role: 'user' | 'assistant'; content: string; created_at: number; elapsed_ms?: number; model_elapsed_ms?: number; reasoning_content?: string; interrupted?: boolean; thinking?: boolean; reasoning_effort?: 'low' | 'high' | 'max'; tool_events?: ToolEvent[]; uploaded_file?: UploadInvitation }
type ChatProgress = { interaction?: ClarificationRequest | VerificationRequest | null; status: 'running' | 'awaiting_input'; phase: 'waiting' | 'preparing' | 'summarizing' | 'model' | 'tool' | 'stopping' | 'finishing'; elapsed_ms: number; phase_elapsed_ms: number; model_elapsed_ms: number; model_calls: number; summary_calls: number; tools_completed: number; tool_name: string | null; reasoning_content?: string }
type Conversation = { id: string; title: string; user_id: string; workspace_id: string; created_at: number; updated_at: number; model: string; provider_name: string; messages: ChatMessage[] }
type ConversationSummary = Pick<Conversation, 'id' | 'title' | 'created_at' | 'updated_at' | 'model'>
type UploadInvitation = { upload_request_id: string; purpose: string; accepted_extensions: string[]; max_size_bytes: number; expires_at: number; retention_hours: number; status: 'pending' | 'receiving' | 'ready' | 'expired' | 'deleted'; conversation_id?: string; filename?: string; size?: number; file_id?: string; path?: string; sha256?: string }
type UploadProgress = { upload_request_id: string; filename?: string; sent: number; total: number; percent: number }

interface Window {
  desktop?: {
    updates: {
      state(): Promise<UpdateStatus>
      check(): Promise<UpdateStatus>
      install(): Promise<UpdateStatus>
      onStatus(callback: (status: UpdateStatus) => void): () => void
    }
    clipboard: {
      writeText(value: string): Promise<void>
    }
    service: {
      request<T>(action: string, value?: unknown): Promise<ServiceResult<T>>
      onUploadProgress(callback: (progress: UploadProgress) => void): () => void
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
