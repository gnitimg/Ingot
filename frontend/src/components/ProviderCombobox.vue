<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { providerPresetsFor, type ProviderCapability, type ProviderPreset } from "../providers";

const props = withDefaults(defineProps<{
  modelValue: string;
  capability: ProviderCapability;
  disabled?: boolean;
  required?: boolean;
  placeholder?: string;
}>(), {
  disabled: false,
  required: false,
  placeholder: "搜索提供商或输入 https://…",
});

const emit = defineEmits<{
  "update:modelValue": [value: string];
}>();

const inputRef = ref<HTMLInputElement | null>(null);
const open = ref(false);
const searching = ref(false);
const highlightedIndex = ref(0);

const presets = computed(() => providerPresetsFor(props.capability));
const normalizedQuery = computed(() => props.modelValue.trim().toLocaleLowerCase());
const filteredPresets = computed(() => {
  if (!searching.value || !normalizedQuery.value) return presets.value;
  const tokens = normalizedQuery.value.split(/\s+/).filter(Boolean);
  return presets.value.filter((provider) => {
    const haystack = [
      provider.label,
      provider.url,
      provider.meta,
      ...provider.aliases,
    ].join(" ").toLocaleLowerCase();
    return tokens.every((token) => haystack.includes(token));
  });
});
const hasCustomValue = computed(() => searching.value && normalizedQuery.value.length > 0);

function showPresets(): void {
  if (props.disabled) return;
  searching.value = false;
  highlightedIndex.value = 0;
  open.value = true;
  nextTick(() => inputRef.value?.select());
}

function updateValue(event: Event): void {
  const value = (event.target as HTMLInputElement).value;
  searching.value = true;
  highlightedIndex.value = 0;
  open.value = true;
  emit("update:modelValue", value);
}

function selectProvider(provider: ProviderPreset): void {
  emit("update:modelValue", provider.url);
  searching.value = false;
  open.value = false;
}

function closeList(): void {
  window.setTimeout(() => {
    open.value = false;
    searching.value = false;
  }, 100);
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") {
    open.value = false;
    return;
  }
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    open.value = true;
    if (!filteredPresets.value.length) return;
    const direction = event.key === "ArrowDown" ? 1 : -1;
    highlightedIndex.value = (
      highlightedIndex.value + direction + filteredPresets.value.length
    ) % filteredPresets.value.length;
    return;
  }
  if (event.key === "Enter" && open.value && filteredPresets.value.length) {
    event.preventDefault();
    const provider = filteredPresets.value[highlightedIndex.value];
    if (provider) selectProvider(provider);
  }
}
</script>

<template>
  <div class="provider-combobox">
    <div class="provider-input-wrap">
      <input
        ref="inputRef"
        :value="modelValue"
        type="url"
        inputmode="url"
        autocomplete="off"
        spellcheck="false"
        role="combobox"
        aria-autocomplete="list"
        :aria-expanded="open"
        :disabled="disabled"
        :required="required"
        :placeholder="placeholder"
        @focus="showPresets"
        @blur="closeList"
        @input="updateValue"
        @keydown="handleKeydown"
      >
      <span class="provider-chevron" aria-hidden="true">⌄</span>
    </div>

    <div v-if="open" class="provider-menu" role="listbox">
      <button
        v-for="(provider, index) in filteredPresets"
        :key="provider.id"
        type="button"
        class="provider-option"
        :class="{ highlighted: index === highlightedIndex, selected: provider.url === modelValue }"
        role="option"
        :aria-selected="provider.url === modelValue"
        @pointerenter="highlightedIndex = index"
        @pointerdown.prevent="selectProvider(provider)"
      >
        <span class="provider-option-head">
          <strong>{{ provider.label }}</strong>
          <small>{{ provider.meta }}</small>
        </span>
        <code>{{ provider.url }}</code>
      </button>

      <div v-if="!filteredPresets.length && hasCustomValue" class="provider-custom">
        <strong>使用自定义提供商地址</strong>
        <code>{{ modelValue }}</code>
        <small>未匹配到预设；保存后会直接使用当前输入。</small>
      </div>
    </div>
  </div>
</template>

<style scoped>
.provider-combobox {
  position: relative;
  min-width: 0;
}

.provider-input-wrap {
  position: relative;
}

.provider-input-wrap input {
  padding-right: 34px;
}

.provider-chevron {
  position: absolute;
  top: 50%;
  right: 12px;
  color: #7e8781;
  pointer-events: none;
  transform: translateY(-56%);
  font-size: 16px;
}

.provider-menu {
  position: absolute;
  z-index: 80;
  top: calc(100% + 5px);
  right: 0;
  left: 0;
  max-height: 300px;
  overflow-y: auto;
  border: 1px solid var(--ink);
  background: var(--white);
  box-shadow: 7px 7px 0 rgba(24, 35, 31, .16);
}

.provider-option {
  width: 100%;
  display: grid;
  gap: 5px;
  padding: 10px 12px;
  border: 0;
  border-bottom: 1px solid var(--line);
  background: transparent;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
}

.provider-option:last-child {
  border-bottom: 0;
}

.provider-option:hover,
.provider-option.highlighted {
  background: #eef2df;
}

.provider-option.selected {
  box-shadow: inset 3px 0 0 var(--accent-dark);
}

.provider-option-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.provider-option strong {
  min-width: 0;
  font-size: 12px;
  font-weight: 700;
}

.provider-option small {
  flex: 0 0 auto;
  color: var(--muted);
  font-size: 9px;
}

.provider-option code,
.provider-custom code {
  overflow: hidden;
  color: #6d7770;
  text-overflow: ellipsis;
  white-space: nowrap;
  font: 9px ui-monospace, SFMono-Regular, Consolas, monospace;
}

.provider-custom {
  display: grid;
  gap: 6px;
  padding: 13px;
  border-left: 3px solid var(--accent-dark);
  background: #f0f1e8;
}

.provider-custom strong {
  font-size: 11px;
}

.provider-custom small {
  color: var(--muted);
  font-size: 9px;
}
</style>
