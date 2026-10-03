import { ApiError } from '../../api/errors'

const MESSAGES: Record<string, string> = {
  invalid_credentials: 'Usuário ou senha incorretos.',
  invalid_setup_token: 'Token de setup inválido. Copie de novo do log do servidor.',
  setup_completed: 'Esta instância já tem um admin. Entre com usuário e senha.',
  weak_password: 'A senha precisa ter entre 10 e 256 caracteres.',
  invalid_username: 'Use de 3 a 64 caracteres: letras minúsculas, números, ponto, hífen ou sublinhado.',
  username_taken: 'Esse usuário já existe.',
  last_admin: 'A instância precisa de pelo menos um admin ativo.',
}

export function authMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'login_locked') {
      const minutes = Math.max(1, Math.ceil((error.retryAfter ?? 60) / 60))
      return `Muitas tentativas. Tente de novo em ${minutes} min.`
    }
    if (MESSAGES[error.code]) return MESSAGES[error.code]
    if (error.status === 0) return 'Sem conexão com o servidor. Tente de novo.'
  }
  return 'Não foi possível concluir. Tente de novo.'
}
