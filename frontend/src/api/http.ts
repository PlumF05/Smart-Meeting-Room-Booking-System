import axios from "axios";

export class ApiError extends Error {
  readonly status: number | null;
  readonly fieldErrors: Record<string, string>;

  constructor(
    status: number | null,
    message: string,
    fieldErrors: Record<string, string> = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.fieldErrors = fieldErrors;
  }
}

const labels: Record<string, string> = {
  username: "登录名",
  full_name: "姓名",
  identity_type: "师生身份",
  password: "密码",
  confirm_password: "确认密码",
  room_id: "会议室编号",
  title: "会议主题",
  purpose: "用途",
  attendees: "预计人数",
  date: "使用日期",
  start_time: "开始时间",
  end_time: "结束时间",
  required_equipment: "所需设备",
  reason: "取消原因",
};

function normalizeError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  if (!axios.isAxiosError(error))
    return new ApiError(null, "操作未完成，请重试。");
  if (error.code === "ECONNABORTED" || error.code === "ETIMEDOUT") {
    return new ApiError(null, "请求超时，请稍后重试。");
  }
  if (!error.response)
    return new ApiError(null, "无法连接服务，请检查连接后重试。");

  const status = error.response.status;
  const isReservation = /^\/?reservations(?:\/|$)/.test(
    error.config?.url ?? "",
  );
  const detail: unknown = error.response.data?.detail;
  const fieldErrors: Record<string, string> = {};
  if (status === 422 && Array.isArray(detail)) {
    for (const item of detail) {
      // 设备数组错误的最后一项可能是下标，字段名在body/query之后。
      const field = Array.isArray(item?.loc) ? item.loc[1] : undefined;
      if (typeof field !== "string" || !labels[field]) continue;
      const label = labels[field];
      let message = `${label}填写不符合要求。`;
      if (item.type === "missing") message = `请填写${label}。`;
      else if (["string_too_short", "too_short"].includes(item.type)) {
        message = field.includes("password")
          ? `${label}至少需要8个字符。`
          : `${label}不能为空。`;
      } else if (["string_too_long", "too_long"].includes(item.type)) {
        message = `${label}超过允许长度。`;
      } else if (field === "identity_type") message = "请选择教师或学生。";
      fieldErrors[field] = message;
    }
    const mismatch = detail.some(
      (item) => item?.msg === "Value error, 两次输入的密码不一致",
    );
    if (mismatch) fieldErrors.confirm_password = "两次输入的密码不一致。";
    const message =
      isReservation && Object.keys(fieldErrors).length
        ? Object.values(fieldErrors).join(" ")
        : mismatch
          ? "两次输入的密码不一致。"
          : "请检查表单中的填写内容。";
    return new ApiError(422, message, fieldErrors);
  }
  const fallback =
    status === 401
      ? "登录已失效，请重新登录。"
      : status === 409
        ? isReservation
          ? "预约状态或时段发生冲突，请刷新记录后核对。"
          : "登录名已存在，请更换登录名。"
        : status >= 500
          ? "服务暂不可用，请稍后重试。"
          : "操作未完成，请重试。";
  // 只保留服务器明确返回的简短提示，不传播可能带密码的Axios请求配置。
  return new ApiError(status, typeof detail === "string" ? detail : fallback);
}

export const http = axios.create({
  baseURL: "/api",
  timeout: 10_000,
  withCredentials: true,
});

http.interceptors.response.use(
  (response) => response,
  (error) => Promise.reject(normalizeError(error)),
);

export function getErrorMessage(error: unknown): string {
  return normalizeError(error).message;
}
