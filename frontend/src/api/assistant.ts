import { http } from './http.ts'

export interface RuleSource {
  id: string
  section: string
  title: string
  version: string
  excerpt: string
}

export interface RulesAnswer {
  answer: string
  grounded: boolean
  sources: RuleSource[]
  knowledge_version: string
}

export const assistantApi = {
  async askRules(question: string, csrfToken: string, signal?: AbortSignal): Promise<RulesAnswer> {
    const { data } = await http.post<RulesAnswer>('/assistant/rules/ask', { question }, {
      headers: { 'X-CSRF-Token': csrfToken }, timeout: 65_000, signal,
    })
    return data
  },
}
