<script setup>
import { computed, ref } from 'vue'
import { ElDialog } from 'element-plus'
import { rooms } from './rooms.js'

const today = new Date()
const todayLabel = new Intl.DateTimeFormat('zh-CN', {
  month: 'long', day: 'numeric', weekday: 'long',
}).format(today)
const localDate = [today.getFullYear(), today.getMonth() + 1, today.getDate()]
  .map(value => String(value).padStart(2, '0')).join('-')
const date = ref(localDate)
const start = ref('14:00')
const end = ref('15:00')
const people = ref(4)
const onlyAvailable = ref(false)
const view = ref('rooms')
const selected = ref(null)
const dialogOpen = ref(false)
const notice = ref('')
const purpose = ref('')
const bookings = ref([])
const demoOccupancy = [{ roomId: rooms[0].id, date: localDate, start: '14:00', end: '15:30' }]

const slots = Array.from({ length: 57 }, (_, index) => {
  const minutes = 480 + index * 15
  return [Math.floor(minutes / 60), minutes % 60]
    .map(value => String(value).padStart(2, '0')).join(':')
})
const minutes = value => Number(value.slice(0, 2)) * 60 + Number(value.slice(3))
const overlaps = (booking, buffer = 0) => booking.date === date.value
  && minutes(start.value) < minutes(booking.end) + buffer
  && minutes(end.value) + buffer > minutes(booking.start)
const conflict = room => [...demoOccupancy, ...bookings.value]
  .some(booking => booking.roomId === room.id && overlaps(booking, 10))
const valid = computed(() => Boolean(date.value) && date.value >= localDate
  && Number.isInteger(people.value) && people.value >= 1 && people.value <= 999
  && minutes(end.value) > minutes(start.value)
  && minutes(end.value) - minutes(start.value) <= 240)
const matches = computed(() => rooms.filter(room => room.capacity >= people.value
  && (!onlyAvailable.value || (valid.value && !conflict(room)))))
const availableCount = computed(() => valid.value
  ? rooms.filter(room => room.capacity >= people.value && !conflict(room)).length : 0)

function openBooking(room) {
  selected.value = room
  purpose.value = ''
  dialogOpen.value = true
}

function book() {
  if (!valid.value || conflict(selected.value) || !purpose.value.trim()) return
  if (bookings.value.some(booking => overlaps(booking))) {
    notice.value = '你在该时段已有预约，请调整时间。'
    dialogOpen.value = false
    return
  }
  bookings.value.push({
    id: crypto.randomUUID(),
    roomId: selected.value.id,
    roomName: selected.value.name,
    date: date.value,
    start: start.value,
    end: end.value,
    people: people.value,
    purpose: purpose.value.trim(),
  })
  dialogOpen.value = false
  notice.value = '预约申请已添加，可在「我的预约」中查看。'
  view.value = 'bookings'
}

function cancel(id) {
  bookings.value = bookings.value.filter(booking => booking.id !== id)
  notice.value = '预约已取消。'
}

function switchView(next) {
  notice.value = ''
  view.value = next
}
</script>

<template>
  <header class="top">
    <div class="top-inner">
      <div class="brand">
        <span class="mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 19V6l8-3 8 3v13M2 19h20M9 19v-5h6v5M8 8h2m4 0h2m-8 3h2m4 0h2"/></svg></span>
        <span>学院会议室预约 <small>SMRBS</small></span>
      </div>
      <nav aria-label="主导航">
        <button type="button" :aria-current="view === 'rooms' ? 'page' : undefined" @click="switchView('rooms')">会议室</button>
        <button type="button" :aria-current="view === 'bookings' ? 'page' : undefined" @click="switchView('bookings')">我的预约 <span v-if="bookings.length" class="badge">{{ bookings.length }}</span></button>
      </nav>
    </div>
  </header>

  <main class="shell">
    <section class="intro">
      <div>
        <p class="date">{{ todayLabel }}</p>
        <h1>{{ view === 'rooms' ? '预约学院会议室' : '我的预约' }}</h1>
        <p class="subtitle">{{ view === 'rooms' ? '按时段与人数查找适合的会议室。' : '查看本次演示中提交的预约申请。' }}</p>
      </div>
      <svg class="plan" viewBox="0 0 350 148" fill="none" aria-hidden="true">
        <path d="M15 12h322v124H15zM30 26h292v96H30zM125 26v96m90-96v96" stroke="#70a8b6" stroke-width="1.3"/>
        <path d="M215 26h107v96H215z" fill="#28566b" stroke="#8bced0" stroke-width="1.5"/>
        <g stroke="#8bced0" stroke-width="1.3"><rect x="56" y="55" width="45" height="36" rx="9"/><rect x="149" y="51" width="42" height="44" rx="8"/><rect x="244" y="49" width="48" height="49" rx="10"/><path d="M258 42h21m-21 63h21m-43-43v23m64-23v23M65 49h27M65 97h27m65-52h26m-26 56h26"/></g>
        <path d="M48 113h52m48 0h43m53 0h48" stroke="#70a8b6" stroke-dasharray="3 4"/>
      </svg>
    </section>

    <p v-if="notice" class="notice" role="status">{{ notice }}</p>

    <template v-if="view === 'rooms'">
      <section class="filters" aria-label="会议室筛选">
        <label>预约日期 <input v-model="date" type="date" :min="localDate" /></label>
        <label>开始时间 <select v-model="start"><option v-for="slot in slots.slice(0, -1)" :key="slot">{{ slot }}</option></select></label>
        <label>结束时间 <select v-model="end"><option v-for="slot in slots.slice(1)" :key="slot">{{ slot }}</option></select></label>
        <label>参会人数 <input v-model.number="people" type="number" min="1" max="999" /></label>
        <label class="checkbox"><input v-model="onlyAvailable" type="checkbox" /> 仅显示可预约</label>
      </section>
      <p v-if="!valid" class="error" role="alert">请填写有效日期和人数，并选择不超过 4 小时的时段。</p>

      <section class="directory" aria-labelledby="directory-title">
        <div class="section-head">
          <h2 id="directory-title">会议室一览</h2>
          <span>{{ valid ? availableCount + ' 间可预约' : '请选择有效时段' }}</span>
        </div>
        <p v-if="!matches.length" class="empty">没有符合条件的会议室。可以减少人数、调整时段或取消「仅显示可预约」。</p>
        <article v-for="room in matches" :key="room.id" class="room">
          <div class="room-code" :class="room.tone">{{ room.id }}</div>
          <div class="room-info">
            <h3>{{ room.name }}</h3>
            <p>{{ room.building }} <span aria-hidden="true">/</span> 容纳 {{ room.capacity }} 人</p>
            <ul class="tags" aria-label="设备"><li v-for="item in room.equipment" :key="item">{{ item }}</li></ul>
          </div>
          <div class="room-action">
            <span class="status" :class="{ busy: !valid || conflict(room) }">{{ !valid ? '时段待确认' : conflict(room) ? '时段冲突' : '可预约' }}</span>
            <button type="button" class="primary" :disabled="!valid || conflict(room)" @click="openBooking(room)">申请预约</button>
          </div>
        </article>
      </section>
    </template>

    <section v-else class="directory" aria-labelledby="bookings-title">
      <div class="section-head"><h2 id="bookings-title">预约记录</h2><span>{{ bookings.length }} 条申请</span></div>
      <div v-if="!bookings.length" class="empty">
        <p>你还没有预约记录。</p>
        <button type="button" class="primary" @click="switchView('rooms')">查找会议室</button>
      </div>
      <article v-for="booking in bookings" :key="booking.id" class="room">
        <div class="room-code blue">{{ booking.roomId }}</div>
        <div class="room-info">
          <h3>{{ booking.roomName }}</h3>
          <p>{{ booking.date }} / {{ booking.start }}–{{ booking.end }} / {{ booking.people }} 人</p>
          <p class="purpose">{{ booking.purpose }}</p>
        </div>
        <div class="room-action">
          <span class="status pending">待审批</span>
          <button type="button" class="secondary" @click="cancel(booking.id)">取消预约</button>
        </div>
      </article>
    </section>

    <footer>课程项目演示 · 预约记录仅保留在当前页面</footer>
  </main>

  <ElDialog v-model="dialogOpen" title="确认预约申请" width="min(450px, 92vw)">
    <div v-if="selected" class="dialog-content">
      <strong>{{ selected.name }}</strong>
      <p>{{ date }} / {{ start }}–{{ end }} / {{ people }} 人</p>
      <label>会议用途 <input v-model="purpose" placeholder="例如：课程小组讨论" maxlength="100" /></label>
    </div>
    <template #footer>
      <button type="button" class="secondary" @click="dialogOpen = false">返回</button>
      <button type="button" class="primary" :disabled="!purpose.trim()" @click="book">提交申请</button>
    </template>
  </ElDialog>
</template>

