const policyTopic = /规则|规定|制度|流程|开放时间|开放时段|提前多久|提前几天|最多|最少|时长限制|必填|选填|调休|节假日|工作日|免审批/u
const policyQuestion = /怎么|怎样|如何|什么|为什么|为何|能不能|可不可以|可否|是否|需不需要|要不要|必须|有没有|多久|多长|几点|几天|吗|呢|[？?]/u
const policySubject = /取消|改约|撤回|审批|审核|批准|驳回|催办|占位|冲突|开放|关闭|停用|启用|权限|教师|学生|注册|登录|密码|主题|用途|设备|人数|预约/u
const availability = /空闲|有空|空着|有没有.*会议室|哪些.*会议室|哪[些间个].*(可约|能约)|查.*(会议室|占位)|还剩/u
const bookingDetails = /今天|明天|后天|下周|周[一二三四五六日天]|上午|下午|晚上|\d{1,2}[:：]\d{2}|[\d一二两三四五六七八九十]+(?:点|位|个人|人)|第[\d一二两三四五六七八九十]+[间个]/u
const bookingAction = /^(?:请|麻烦)?(?:帮我|给我|替我)(?:预约|预订|订|找)|^(?:我要|我想|打算)(?:预约|预订|订)/u
const ruleAction = /(?:怎么|怎样|如何)(?:取消|改约|撤回|催办|审批|预约|预订)|(?:开始|提交|审批|审核)(?:后|前|之后|之前).*(?:取消|撤回|审批|催办|占位)|(?:需要|必须|要不要|用不用|是否)(?:先|再|都|一定要)?(?:填写|填|审批|审核)|(?:能|可以|可否|是否).*(?:取消|撤回|催办)|(?:几|什么时).*(?:开放|关闭)|(?:开放|关闭).*(?:到|几|什么时)/u

/** 仅选择读取路径，提交仍由预约接口的确认机制决定。 */
export function isRuleQuestion(text: string): boolean {
  const message = text.normalize('NFKC').replace(/\s/gu, '')
  if (ruleAction.test(message)) return true
  if (availability.test(message)) return false
  const concreteBooking = bookingAction.test(message) || bookingDetails.test(message)
  if (policyTopic.test(message) && (!concreteBooking || policyQuestion.test(message))) return true
  if (concreteBooking) return false
  return policySubject.test(message) && policyQuestion.test(message)
}
