<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { GraphEdge, GraphNode } from "../types";

interface SimNode extends GraphNode { x: number; y: number; vx: number; vy: number; degree: number; }

const props = defineProps<{ nodes: GraphNode[]; edges: GraphEdge[]; search: string }>();
const canvas = ref<HTMLCanvasElement | null>(null);
const wrapper = ref<HTMLDivElement | null>(null);
const tooltip = ref<{ node: SimNode; x: number; y: number } | null>(null);
const simNodes = ref<SimNode[]>([]);
const simEdges = ref<GraphEdge[]>([]);
const width = ref(1);
const height = ref(1);
let nodeMap = new Map<string, SimNode>();
let frame = 0;
let tickCount = 0;
let resizeObserver: ResizeObserver | undefined;

const colors = ["#477ae8", "#df6c42", "#87a91c", "#8c61c9", "#d59b1d", "#299d8f", "#c65c8b", "#69736d"];
const types = computed(() => [...new Set(props.nodes.map(node => node.type || "其他"))].slice(0, 8));

function typeColor(type: string) {
  let hash = 0;
  for (const char of type || "其他") hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
  return colors[Math.abs(hash) % colors.length];
}

function resize() {
  if (!canvas.value || !wrapper.value) return;
  const rect = wrapper.value.getBoundingClientRect();
  width.value = Math.max(1, rect.width);
  height.value = Math.max(1, rect.height);
  const dpr = window.devicePixelRatio || 1;
  canvas.value.width = width.value * dpr;
  canvas.value.height = height.value * dpr;
  draw();
}

function setData() {
  cancelAnimationFrame(frame);
  const cx = width.value / 2;
  const cy = height.value / 2;
  simNodes.value = props.nodes.map((node, index) => ({
    ...node,
    x: cx + Math.cos(index * 2.4) * (35 + index * 0.7),
    y: cy + Math.sin(index * 2.4) * (35 + index * 0.7),
    vx: 0,
    vy: 0,
    degree: 0,
  }));
  nodeMap = new Map(simNodes.value.map(node => [node.id, node]));
  simEdges.value = props.edges.filter(edge => nodeMap.has(edge.source) && nodeMap.has(edge.target));
  for (const edge of simEdges.value) {
    nodeMap.get(edge.source)!.degree++;
    nodeMap.get(edge.target)!.degree++;
  }
  tickCount = 0;
  animate();
}

function simulate() {
  const nodes = simNodes.value;
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      let dx = nodes[j].x - nodes[i].x;
      let dy = nodes[j].y - nodes[i].y;
      const distance2 = dx * dx + dy * dy + 0.1;
      const force = Math.min(2.2, 900 / distance2);
      const distance = Math.sqrt(distance2);
      dx /= distance; dy /= distance;
      nodes[i].vx -= dx * force; nodes[i].vy -= dy * force;
      nodes[j].vx += dx * force; nodes[j].vy += dy * force;
    }
  }
  for (const edge of simEdges.value) {
    const a = nodeMap.get(edge.source)!;
    const b = nodeMap.get(edge.target)!;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const distance = Math.sqrt(dx * dx + dy * dy) || 1;
    const force = (distance - 75) * 0.006;
    a.vx += dx / distance * force; a.vy += dy / distance * force;
    b.vx -= dx / distance * force; b.vy -= dy / distance * force;
  }
  for (const node of nodes) {
    node.vx += (width.value / 2 - node.x) * 0.0009;
    node.vy += (height.value / 2 - node.y) * 0.0009;
    node.vx *= 0.86; node.vy *= 0.86;
    node.x = Math.max(12, Math.min(width.value - 12, node.x + node.vx));
    node.y = Math.max(12, Math.min(height.value - 12, node.y + node.vy));
  }
}

function draw() {
  const element = canvas.value;
  if (!element) return;
  const context = element.getContext("2d");
  if (!context) return;
  const dpr = window.devicePixelRatio || 1;
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, width.value, height.value);
  context.lineWidth = 0.7;
  context.strokeStyle = "rgba(72,83,77,.27)";
  for (const edge of simEdges.value) {
    const a = nodeMap.get(edge.source)!;
    const b = nodeMap.get(edge.target)!;
    context.beginPath(); context.moveTo(a.x, a.y); context.lineTo(b.x, b.y); context.stroke();
  }
  const query = props.search.trim().toLocaleLowerCase();
  for (const node of simNodes.value) {
    const match = Boolean(query && node.name.toLocaleLowerCase().includes(query));
    const hovered = tooltip.value?.node.id === node.id;
    const radius = 3.5 + Math.min(6, Math.sqrt(node.degree + Number(node.mentions || 0)));
    context.beginPath();
    context.arc(node.x, node.y, match || hovered ? radius + 3 : radius, 0, Math.PI * 2);
    context.fillStyle = typeColor(node.type); context.fill();
    if (match || hovered) { context.lineWidth = 2; context.strokeStyle = "#18231f"; context.stroke(); }
    if (node.degree > 2 || match || hovered || simNodes.value.length < 25) {
      context.font = `${match ? 700 : 500} 10px system-ui`;
      context.fillStyle = "#26322d";
      context.fillText(node.name.slice(0, 20), node.x + radius + 4, node.y + 3);
    }
  }
}

function animate() {
  if (tickCount++ < 160) {
    simulate(); draw(); frame = requestAnimationFrame(animate);
  } else draw();
}

function onMove(event: MouseEvent) {
  if (!canvas.value) return;
  const rect = canvas.value.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const node = simNodes.value.find(item => (item.x - x) ** 2 + (item.y - y) ** 2 < 120);
  tooltip.value = node ? { node, x: Math.min(width.value - 260, x + 15), y: Math.max(8, y - 10) } : null;
  draw();
}

watch(() => [props.nodes, props.edges], async () => { await nextTick(); resize(); setData(); }, { deep: true });
watch(() => props.search, draw);
onMounted(async () => {
  await nextTick();
  resizeObserver = new ResizeObserver(resize);
  if (wrapper.value) resizeObserver.observe(wrapper.value);
  resize(); setData();
});
onBeforeUnmount(() => { cancelAnimationFrame(frame); resizeObserver?.disconnect(); });
</script>

<template>
  <div ref="wrapper" class="graph-canvas-wrap">
    <canvas ref="canvas" @mousemove="onMove" @mouseleave="tooltip = null; draw()" />
    <div v-if="!nodes.length" class="graph-empty">
      <strong>尚无图谱数据</strong><span>导入文档后点击「构建 GraphRAG」</span>
    </div>
    <div class="graph-legend">
      <span v-for="type in types" :key="type" class="legend-item"><i :style="{ background: typeColor(type) }" />{{ type }}</span>
    </div>
    <div v-if="tooltip" class="node-tooltip" :style="{ left: `${tooltip.x}px`, top: `${tooltip.y}px` }">
      <strong>{{ tooltip.node.name }} · {{ tooltip.node.type }}</strong>
      <span>{{ tooltip.node.description || `${tooltip.node.mentions || 0} 个文本证据` }}</span>
    </div>
  </div>
</template>
