<script setup lang="ts">
import { ElPagination } from "element-plus";

defineProps<{ currentPage: number; pageSize: number; total: number; disabled?: boolean }>();
const emit = defineEmits<{ "current-change": [page: number]; "size-change": [size: number] }>();
const sizes = [10, 20, 50, 100];
</script>

<template>
  <div class="list-pagination">
    <ElPagination
      :current-page="currentPage"
      :page-size="pageSize"
      :total="total"
      :disabled="disabled"
      layout="total, prev, pager, next"
      @update:current-page="emit('current-change', $event)"
    />
    <label class="page-size">
      每页：
      <select
        :value="pageSize"
        :disabled="disabled"
        @change="emit('size-change', Number(($event.target as HTMLSelectElement).value))"
      >
        <option v-for="size in sizes" :key="size" :value="size">{{ size }}</option>
      </select>
    </label>
  </div>
</template>

<style scoped>
.list-pagination {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 14px;
}
.page-size {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
.page-size select {
  min-width: 4.5rem;
  padding: 4px 6px;
  font: inherit;
  color: inherit;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color);
  border-radius: 3px;
}
.page-size select:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 1px;
}
</style>
