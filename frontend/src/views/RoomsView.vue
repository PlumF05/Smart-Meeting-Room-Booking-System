<script setup lang="ts">
import { computed, onMounted, watch } from "vue";
import { useRouter } from "vue-router";
import {
  ElAlert,
  ElButton,
  ElDialog,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElInput,
  ElOption,
  ElSelect,
  ElTable,
  ElTableColumn,
  ElTag,
} from "element-plus";
import { useAuthStore } from "../stores/auth.ts";
import { useRoomBrowser } from "../composables/useRoomBrowser.ts";
import BookingDialog from "../components/BookingDialog.vue";
import { ref, reactive } from "vue";
import type { RoomInfo } from "../types/rooms.ts";
import { roomTimeOptions } from "../composables/roomWindow.ts";

const bookingDraft = reactive({
  title: "",
  purpose: "",
  attendees: 1,
  date: "",
  start_time: "",
  end_time: "",
  required_equipment: [] as string[],
});
function clearBookingDraft() {
  Object.assign(bookingDraft, {
    title: "",
    purpose: "",
    attendees: 1,
    date: "",
    start_time: "",
    end_time: "",
    required_equipment: [],
  });
}
const selectedRoom = ref<RoomInfo | null>(null);
const auth = useAuthStore();
watch(
  () => [auth.user?.id, auth.status, auth.sessionChangeCount],
  () => {
    selectedRoom.value = null;
    clearBookingDraft();
  },
);

const router = useRouter();
const {
  draft,
  rooms,
  equipmentOptions,
  loading,
  errorMessage,
  hasLoaded,
  appliedFilters,
  appliedWindow,
  occupancyByRoom,
  windowRules,
  rulesLoading,
  rulesError,
  detailOpen,
  detail,
  detailId,
  detailLoading,
  detailError,
  search,
  resetFilters,
  openDetail,
  closeDetail,
  clearWindowDraft,
  loadWindowRules,
} = useRoomBrowser();
const startOptions = computed(() =>
  roomTimeOptions(windowRules.value, "start"),
);
const endOptions = computed(() => roomTimeOptions(windowRules.value, "end"));

onMounted(() => {
  void loadWindowRules();
});

watch(
  () => auth.status,
  (status) => {
    if (status === "anonymous") void router.replace({ name: "login" });
  },
  { immediate: true },
);

function updateDetailVisibility(visible: boolean) {
  if (!visible) closeDetail();
}

function canBookRoom(room: RoomInfo | undefined) {
  if (!room) return false;
  const occupancy = occupancyByRoom.value[room.id];
  return (
    room.is_active &&
    (!appliedWindow.value ||
      (occupancy?.is_active === true && occupancy.available))
  );
}

function openBooking(roomId: number) {
  const room = rooms.value.find((item) => item.id === roomId);
  if (!room || !canBookRoom(room)) return;
  const emptyDraft =
    !bookingDraft.title &&
    !bookingDraft.purpose &&
    bookingDraft.attendees === 1 &&
    !bookingDraft.date &&
    !bookingDraft.start_time &&
    !bookingDraft.end_time &&
    !bookingDraft.required_equipment.length;
  if (emptyDraft) {
    Object.assign(bookingDraft, {
      date: appliedWindow.value?.date ?? "",
      start_time: appliedWindow.value?.start_time ?? "",
      end_time: appliedWindow.value?.end_time ?? "",
      attendees: appliedFilters.value.attendees ?? 1,
      required_equipment: [...(appliedFilters.value.equipment ?? [])],
    });
  }
  selectedRoom.value = room;
}

const occupancyTime = (value: string) =>
  new Date(value).toLocaleTimeString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
</script>

<template>
  <section class="rooms-page" aria-labelledby="rooms-title">
    <div class="account-heading rooms-heading">
      <div>
        <h1 id="rooms-title">会议室查询</h1>
      </div>
    </div>

    <section class="room-filters" aria-label="会议室筛选">
      <ElForm label-position="top" :disabled="loading" @submit.prevent="search">
        <div class="filter-grid">
          <ElFormItem label="人数" label-for="room-attendees">
            <ElInput
              id="room-attendees"
              v-model="draft.attendees"
              inputmode="numeric"
              placeholder="人数"
              clearable
            />
          </ElFormItem>
          <ElFormItem label="所需设备" label-for="room-equipment">
            <ElSelect
              id="room-equipment"
              v-model="draft.equipment"
              multiple
              filterable
              allow-create
              default-first-option
              :reserve-keyword="false"
              :multiple-limit="10"
              placeholder="选择设备，或输入名称"
              no-data-text="可输入设备名称"
            >
              <ElOption
                v-for="equipment in equipmentOptions"
                :key="equipment"
                :label="equipment"
                :value="equipment"
              />
            </ElSelect>
          </ElFormItem>
          <ElFormItem label="资源状态" label-for="room-state">
            <ElSelect id="room-state" v-model="draft.state">
              <ElOption label="全部状态" value="all" />
              <ElOption label="启用" value="active" />
              <ElOption label="停用" value="inactive" />
            </ElSelect>
          </ElFormItem>
          <div class="filter-actions">
            <ElButton type="primary" native-type="submit" :loading="loading"
              >查询</ElButton
            >
            <ElButton :disabled="loading" @click="resetFilters">重置</ElButton>
          </div>
        </div>
        <div class="time-filter-grid">
          <ElFormItem label="使用日期" label-for="room-date">
            <ElInput
              id="room-date"
              v-model="draft.date"
              type="date"
              :min="windowRules?.min_date"
              :max="windowRules?.max_date"
              clearable
            />
          </ElFormItem>
          <ElFormItem label="开始时间" label-for="room-start">
            <ElSelect
              id="room-start"
              v-model="draft.start_time"
              clearable
              filterable
              :disabled="!windowRules"
              no-match-text="没有匹配的时刻"
            >
              <ElOption
                v-for="time in startOptions"
                :key="time"
                :label="time"
                :value="time"
              />
            </ElSelect>
          </ElFormItem>
          <ElFormItem label="结束时间" label-for="room-end">
            <ElSelect
              id="room-end"
              v-model="draft.end_time"
              clearable
              filterable
              :disabled="!windowRules"
              no-match-text="没有匹配的时刻"
            >
              <ElOption
                v-for="time in endOptions"
                :key="time"
                :label="time"
                :value="time"
              />
            </ElSelect>
          </ElFormItem>
          <div class="clear-time-action">
            <ElButton link type="primary" @click="clearWindowDraft"
              >清除</ElButton
            >
          </div>
        </div>
      </ElForm>
      <p v-if="rulesLoading" class="room-query-note" role="status">
        正在读取服务器时间规则…
      </p>
      <div v-else-if="rulesError" class="time-rules-error" role="alert">
        <p>{{ rulesError }} 仍可不填时间，查询会议室资料。</p>
        <ElButton link type="primary" @click="loadWindowRules"
          >重新读取时间规则</ElButton
        >
      </div>
    </section>

    <section
      class="room-results"
      aria-labelledby="room-results-title"
      :aria-busy="loading"
    >
      <div class="results-heading">
        <h2 id="room-results-title">
          会议室列表<span v-if="hasLoaded">{{ rooms.length }} 间</span>
        </h2>
      </div>
      <div v-if="loading" class="rooms-feedback" role="status">
        正在查询
      </div>
      <div v-else-if="errorMessage" class="rooms-feedback">
        <ElAlert
          :title="errorMessage"
          type="error"
          :closable="false"
          show-icon
          role="alert"
        />
        <ElButton class="feedback-button" @click="search">重新查询</ElButton>
      </div>
      <template v-else-if="hasLoaded">
        <p v-if="appliedWindow" class="checked-window" role="status">
          {{ appliedWindow.date }} {{ appliedWindow.start_time }}—{{
            appliedWindow.end_time
          }}
          以下为本次查询的结果
        </p>
        <ElEmpty v-if="!rooms.length" description="没有符合条件的会议室">
          <ElButton @click="resetFilters">清除筛选</ElButton>
        </ElEmpty>
        <ElTable
          v-else
          :data="rooms"
          row-key="id"
          class="rooms-table"
          aria-label="会议室查询结果"
        >
          <ElTableColumn label="会议室" min-width="170">
            <template #default="{ row }">
              <span class="room-name">{{ row.name }}</span>
              <span class="room-id">id： {{ row.id }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="location" label="地点" min-width="180" />
          <ElTableColumn label="容量" width="82" align="right">
            <template #default="{ row }"
              ><span class="room-capacity">{{ row.capacity }}</span>
              人</template
            >
          </ElTableColumn>
          <ElTableColumn label="设备" min-width="220">
            <template #default="{ row }">
              <div v-if="row.equipment.length" class="equipment-tags">
                <ElTag
                  v-for="equipment in row.equipment"
                  :key="equipment"
                  type="info"
                  effect="plain"
                  >{{ equipment }}</ElTag
                >
              </div>
              <span v-else class="room-muted">未配置设备</span>
            </template>
          </ElTableColumn>
          <ElTableColumn label="资源状态" width="102">
            <template #default="{ row }"
              ><span
                class="resource-state"
                :data-status-cue="row.is_active ? 'active' : 'inactive'"
                :class="{ inactive: !row.is_active }"
                >{{ row.is_active ? "正在启用" : "已停用" }}</span
              ></template
            >
          </ElTableColumn>
          <ElTableColumn v-if="appliedWindow" label="所选时段" min-width="230">
            <template #default="{ row }">
              <span
                v-if="!occupancyByRoom[row.id]?.is_active"
                class="room-muted"
                data-status-cue="inactive"
                >停用</span
              >
              <span
                v-else-if="occupancyByRoom[row.id]?.available"
                class="resource-state"
                data-status-cue="available"
                >可预约</span
              >
              <div
                v-for="(item, index) in occupancyByRoom[row.id]?.occupancy"
                v-else
                :key="index"
                class="occupancy-slot"
                :data-status-cue="item.status === 'pending' ? 'pending' : 'occupied'"
              >
                {{ item.status === "pending" ? "未审批占位" : "已预约" }}：{{
                  occupancyTime(item.starts_at)
                }}—{{ occupancyTime(item.ends_at) }}
              </div>
            </template>
          </ElTableColumn>
          <ElTableColumn label="操作" width="190" align="right">
            <template #default="{ row }"
              ><ElButton
                link
                type="primary"
                :aria-label="`查看${row.name}详情`"
                @click="openDetail(row.id)"
                >详情</ElButton
              ><ElButton
                v-if="auth.user?.role === 'user'"
                link
                type="primary"
                :disabled="
                  !canBookRoom(rooms.find((room) => room.id === row.id))
                "
                @click="openBooking(row.id)"
                >预约</ElButton
              ></template
            >
          </ElTableColumn>
        </ElTable>
      </template>
    </section>

    <ElDialog
      :model-value="detailOpen"
      title="会议室详情"
      width="620px"
      destroy-on-close
      @update:model-value="updateDetailVisibility"
    >
      <div v-if="detailLoading" class="detail-loading" role="status">
        正在读取会议室资料…
      </div>
      <template v-else-if="detailError">
        <ElAlert
          :title="detailError"
          type="error"
          :closable="false"
          show-icon
          role="alert"
        />
        <ElButton
          v-if="detailId !== null"
          class="feedback-button"
          @click="openDetail(detailId)"
          >重试详情</ElButton
        >
      </template>
      <template v-else-if="detail">
        <div class="room-detail-heading">
          <h2>{{ detail.name }}</h2>
          <span
            class="resource-state"
            :class="{ inactive: !detail.is_active }"
            >{{ detail.is_active ? "启用" : "停用" }}</span
          >
        </div>
        <dl class="room-detail-fields">
          <div>
            <dt>编号</dt>
            <dd>{{ detail.id }}</dd>
          </div>
          <div>
            <dt>容量</dt>
            <dd>{{ detail.capacity }} 人</dd>
          </div>
          <div class="detail-wide">
            <dt>地点</dt>
            <dd>{{ detail.location }}</dd>
          </div>
          <div class="detail-wide">
            <dt>设备</dt>
            <dd>
              {{
                detail.equipment.length
                  ? detail.equipment.join("、")
                  : "未配置设备"
              }}
            </dd>
          </div>
        </dl>
        <p class="detail-note">
          {{ detail.is_active ? "该会议室处于启用状态。" : "该会议室已停用。" }}
        </p>
      </template>
      <template #footer
        ><ElButton @click="closeDetail">关闭</ElButton></template
      >
    </ElDialog>
    <BookingDialog
      v-if="selectedRoom"
      :key="selectedRoom.id"
      :room="selectedRoom"
      :draft="bookingDraft"
      @submitted="clearBookingDraft"
      @close="selectedRoom = null"
    />
  </section>
</template>

<style scoped>
.rooms-page {
  min-width: 960px;
}
.room-filters {
  padding: 24px 26px 18px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: #fff;
}
.filter-grid {
  display: grid;
  grid-template-columns: 150px minmax(240px, 1fr) 145px auto;
  gap: 18px;
  align-items: start;
}
.time-filter-grid {
  display: grid;
  grid-template-columns: 230px 190px 190px 1fr;
  gap: 18px;
  align-items: start;
  margin-top: 22px;
  padding-top: 20px;
  border-top: 1px solid var(--border);
}
.room-filters :deep(.el-form-item) {
  margin-bottom: 0;
}
.room-filters :deep(.el-form-item__label) {
  font-size: 0.875rem;
  color: var(--navy);
}
.room-filters :deep(.el-select) {
  width: 100%;
}
.filter-actions {
  display: flex;
  align-items: center;
  padding-top: 30px;
}
.clear-time-action {
  padding-top: 30px;
  text-align: right;
}
.room-query-note {
  margin: 18px 0 0;
  color: var(--muted);
  font-size: 0.8125rem;
  line-height: 1.8;
}
.server-time-note {
  margin: 5px 0 0;
  color: var(--muted);
  font-size: 0.75rem;
  line-height: 1.8;
}
.time-rule-refresh {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}
.time-rule-refresh :deep(.el-button) {
  flex-shrink: 0;
  font-size: 0.8125rem;
}
.time-rules-error {
  margin-top: 18px;
  color: #8c5931;
  font-size: 0.8125rem;
}
.time-rules-error p {
  margin: 0 0 5px;
}
.checked-window {
  margin: 0;
  padding: 14px 26px;
  background: #f1f7f6;
  border-bottom: 1px solid var(--border);
  color: var(--navy);
  font-size: 0.8125rem;
  line-height: 1.8;
}
.room-results {
  margin-top: 28px;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: #fff;
}
.results-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 22px 26px;
  border-bottom: 1px solid var(--border);
}
.results-heading h2 {
  flex-shrink: 0;
  margin: 0;
  font-size: 1.125rem;
}
.results-heading h2 span {
  margin-left: 14px;
  color: var(--muted);
  font-size: 0.875rem;
  font-weight: 400;
}
.results-heading p {
  margin: 0;
  color: var(--muted);
  font-size: 0.8125rem;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.rooms-feedback {
  padding: 32px 26px;
  color: var(--muted);
}
.feedback-button {
  margin-top: 18px;
}
.rooms-table {
  --el-table-header-bg-color: #f6f9fa;
  --el-table-header-text-color: #536a76;
  --el-table-border-color: #e3eaed;
}
.rooms-table :deep(th.el-table__cell) {
  height: 46px;
  font-weight: 500;
}
.rooms-table :deep(td.el-table__cell) {
  padding-block: 17px;
}
.rooms-table :deep(.cell) {
  padding-inline: 18px;
  overflow-wrap: anywhere;
}
.room-name {
  display: block;
  color: var(--navy);
  font-weight: 600;
}
.room-id {
  display: block;
  margin-top: 5px;
  color: var(--muted);
  font-size: 0.75rem;
}
.room-capacity {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.equipment-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.equipment-tags :deep(.el-tag) {
  height: auto;
  white-space: normal;
  overflow-wrap: anywhere;
  line-height: 1.6;
  padding-block: 2px;
}
.resource-state {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--teal);
  font-size: 0.875rem;
  white-space: nowrap;
}
.resource-state::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
.resource-state.inactive,
.room-muted {
  color: var(--muted);
}
.occupancy-slot {
  color: #8c5931;
  font-size: 0.8125rem;
  line-height: 1.8;
}
.detail-loading {
  padding: 25px 0;
  color: var(--muted);
}
.room-detail-heading {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  align-items: center;
}
.room-detail-heading h2 {
  margin: 4px 0;
  color: var(--navy);
  font-size: 1.4375rem;
  overflow-wrap: anywhere;
}
.room-detail-fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  margin: 28px 0;
}
.room-detail-fields dt {
  margin-bottom: 8px;
  color: var(--muted);
  font-size: 0.8125rem;
}
.room-detail-fields dd {
  margin: 0;
  color: var(--navy);
  font-size: 0.9375rem;
  line-height: 1.8;
  overflow-wrap: anywhere;
}
.detail-wide {
  grid-column: 1 / -1;
}
.detail-note {
  margin: 0;
  padding-top: 18px;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.8125rem;
  line-height: 1.8;
}
</style>
