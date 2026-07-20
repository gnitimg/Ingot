<script setup lang="ts">
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type Simulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { GraphEdge, GraphNode } from "../types";

interface SimNode extends GraphNode, SimulationNodeDatum {
  x: number;
  y: number;
  vx: number;
  vy: number;
  degree: number;
  pinned: boolean;
}

type SimEdge = Omit<GraphEdge, "source" | "target"> & SimulationLinkDatum<SimNode> & {
  source: string | SimNode;
  target: string | SimNode;
  parallelIndex: number;
  parallelCount: number;
};

type Interaction =
  | {
      kind: "node";
      pointerId: number;
      node: SimNode | null;
      offsetX: number;
      offsetY: number;
      startX: number;
      startY: number;
      moved: boolean;
    }
  | {
      kind: "pan";
      pointerId: number;
      startX: number;
      startY: number;
      startPanX: number;
      startPanY: number;
    };

const props = defineProps<{ nodes: GraphNode[]; edges: GraphEdge[]; search: string }>();
const canvas = ref<HTMLCanvasElement | null>(null);
const wrapper = ref<HTMLDivElement | null>(null);
const tooltip = ref<{ node: SimNode; x: number; y: number; selected: boolean } | null>(null);
const simNodes = ref<SimNode[]>([]);
const simEdges = ref<SimEdge[]>([]);
const width = ref(0);
const height = ref(0);
const zoom = ref(1);
const cursor = ref("default");
const selectedNodeId = ref<string | null>(null);
const hoveredNodeId = ref<string | null>(null);
const importantLabelIds = ref(new Set<string>());
let nodeMap = new Map<string, SimNode>();
let simulation: Simulation<SimNode, SimEdge> | null = null;
let panX = 0;
let panY = 0;
let interaction: Interaction | null = null;
let resizeObserver: ResizeObserver | undefined;
let layoutPending = true;
let autoFitPending = false;
let userTransformed = false;

const colors = ["#477ae8", "#df6c42", "#87a91c", "#8c61c9", "#d59b1d", "#299d8f", "#c65c8b", "#69736d"];
const types = computed(() => [...new Set(props.nodes.map(node => node.type || "其他"))].slice(0, 12));
const zoomPercent = computed(() => `${Math.round(zoom.value * 100)}%`);
const dataSignature = computed(() => {
  const nodes = props.nodes.map(node => `${node.id}\u001f${node.name}\u001f${node.type}\u001f${node.mentions}`).join("\u001e");
  const edges = props.edges.map(edge => `${edge.id}\u001f${edge.source}\u001f${edge.target}\u001f${edge.label}`).join("\u001e");
  return `${nodes}\u001d${edges}`;
});

function typeColor(type: string): string {
  let hash = 0;
  for (const char of type || "其他") hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
  return colors[Math.abs(hash) % colors.length] ?? "#69736d";
}

function nodeRadius(node: SimNode): number {
  const evidence = Math.log2(1 + Number(node.mentions || 0)) * 1.4;
  return 8 + Math.min(8, Math.sqrt(node.degree) * 1.7 + evidence);
}

function localPoint(event: PointerEvent | WheelEvent): { x: number; y: number } {
  const rect = canvas.value?.getBoundingClientRect();
  return {
    x: event.clientX - (rect?.left || 0),
    y: event.clientY - (rect?.top || 0),
  };
}

function screenToWorld(x: number, y: number): { x: number; y: number } {
  return { x: (x - panX) / zoom.value, y: (y - panY) / zoom.value };
}

function worldToScreen(x: number, y: number): { x: number; y: number } {
  return { x: x * zoom.value + panX, y: y * zoom.value + panY };
}

function edgeNode(endpoint: string | number | SimNode): SimNode | undefined {
  if (typeof endpoint === "object") return endpoint;
  return nodeMap.get(String(endpoint));
}

function hitTest(x: number, y: number): SimNode | null {
  let closest: SimNode | null = null;
  let closestDistance = Number.POSITIVE_INFINITY;
  for (const node of simNodes.value) {
    const point = worldToScreen(node.x, node.y);
    const distance = (point.x - x) ** 2 + (point.y - y) ** 2;
    const hitRadius = Math.max(12, nodeRadius(node) * zoom.value + 5);
    if (distance <= hitRadius ** 2 && distance < closestDistance) {
      closest = node;
      closestDistance = distance;
    }
  }
  return closest;
}

function updateTooltip() {
  const hovered = hoveredNodeId.value ? nodeMap.get(hoveredNodeId.value) : undefined;
  const selected = selectedNodeId.value ? nodeMap.get(selectedNodeId.value) : undefined;
  const node = hovered || selected;
  if (!node) {
    tooltip.value = null;
    return;
  }
  const point = worldToScreen(node.x, node.y);
  if (point.x < -30 || point.y < -30 || point.x > width.value + 30 || point.y > height.value + 30) {
    tooltip.value = null;
    return;
  }
  tooltip.value = {
    node,
    x: Math.max(8, Math.min(Math.max(8, width.value - 288), point.x + 18)),
    y: Math.max(8, Math.min(Math.max(8, height.value - 106), point.y - 14)),
    selected: node.id === selectedNodeId.value,
  };
}

function hasUsableSize() {
  return width.value >= 120 && height.value >= 120;
}

function resize() {
  if (!canvas.value || !wrapper.value) return;
  const rect = wrapper.value.getBoundingClientRect();
  if (rect.width < 120 || rect.height < 120) {
    layoutPending = true;
    return;
  }
  const previousWidth = width.value;
  const previousHeight = height.value;
  width.value = rect.width;
  height.value = rect.height;
  const dpr = window.devicePixelRatio || 1;
  canvas.value.width = Math.round(width.value * dpr);
  canvas.value.height = Math.round(height.value * dpr);

  if (layoutPending) {
    setData();
    return;
  }
  if (previousWidth >= 120 && previousHeight >= 120) {
    panX += (width.value - previousWidth) / 2;
    panY += (height.value - previousHeight) / 2;
    simulation?.force("center", forceCenter<SimNode>(width.value / 2, height.value / 2).strength(0.08));
    simulation?.force("x", forceX<SimNode>(width.value / 2).strength(0.04));
    simulation?.force("y", forceY<SimNode>(height.value / 2).strength(0.04));
    simulation?.alpha(0.22).restart();
  }
  draw();
}

function resetView(markAsUserAction = true) {
  if (!simNodes.value.length || !hasUsableSize()) {
    zoom.value = 1;
    panX = 0;
    panY = 0;
    draw();
    return;
  }
  const xs = simNodes.value.map(node => node.x);
  const ys = simNodes.value.map(node => node.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const margin = 86;
  const availableWidth = Math.max(80, width.value - margin * 2);
  const availableHeight = Math.max(80, height.value - margin * 2);
  const nextZoom = Math.min(
    availableWidth / Math.max(1, maxX - minX),
    availableHeight / Math.max(1, maxY - minY),
  );
  zoom.value = Math.max(0.24, Math.min(2.2, nextZoom));
  panX = width.value / 2 - ((minX + maxX) / 2) * zoom.value;
  panY = height.value / 2 - ((minY + maxY) / 2) * zoom.value;
  if (markAsUserAction) userTransformed = true;
  updateTooltip();
  draw();
}

function setZoom(nextZoom: number, anchorX = width.value / 2, anchorY = height.value / 2) {
  const world = screenToWorld(anchorX, anchorY);
  zoom.value = Math.max(0.2, Math.min(5, nextZoom));
  panX = anchorX - world.x * zoom.value;
  panY = anchorY - world.y * zoom.value;
  userTransformed = true;
  updateTooltip();
  draw();
}

function zoomBy(factor: number) {
  setZoom(zoom.value * factor);
}

function initialPosition(index: number, count: number): { x: number; y: number } {
  const angle = index * Math.PI * (3 - Math.sqrt(5));
  const maxRadius = Math.max(70, Math.min(width.value, height.value) * 0.34);
  const radius = count <= 1 ? 0 : 24 + Math.sqrt(index / Math.max(1, count - 1)) * maxRadius;
  return {
    x: width.value / 2 + Math.cos(angle) * radius,
    y: height.value / 2 + Math.sin(angle) * radius,
  };
}

function updateImportantLabels() {
  const limit = Math.min(24, Math.max(10, Math.ceil(Math.sqrt(simNodes.value.length) * 3)));
  importantLabelIds.value = new Set(
    [...simNodes.value]
      .sort((a, b) => (b.degree * 4 + Number(b.mentions || 0)) - (a.degree * 4 + Number(a.mentions || 0)))
      .slice(0, limit)
      .map(node => node.id),
  );
}

function setData(preserveExistingPositions = true) {
  if (!hasUsableSize()) {
    layoutPending = true;
    return;
  }
  layoutPending = false;
  simulation?.stop();
  const previousNodes = nodeMap;
  const previousValues = [...previousNodes.values()].filter(node => Number.isFinite(node.x) && Number.isFinite(node.y));
  const previousSpread = previousValues.length > 1
    ? Math.max(...previousValues.map(node => node.x)) - Math.min(...previousValues.map(node => node.x))
      + Math.max(...previousValues.map(node => node.y)) - Math.min(...previousValues.map(node => node.y))
    : 0;
  const preservePositions = preserveExistingPositions && previousSpread > 20;

  simNodes.value = props.nodes.map((node, index) => {
    const previous = preservePositions ? previousNodes.get(node.id) : undefined;
    const start = initialPosition(index, props.nodes.length);
    return {
      ...node,
      x: previous?.x ?? start.x,
      y: previous?.y ?? start.y,
      vx: 0,
      vy: 0,
      fx: previous?.pinned ? previous.x : null,
      fy: previous?.pinned ? previous.y : null,
      degree: 0,
      pinned: previous?.pinned ?? false,
    };
  });
  nodeMap = new Map(simNodes.value.map(node => [node.id, node]));
  simEdges.value = props.edges
    .filter(edge => nodeMap.has(edge.source) && nodeMap.has(edge.target))
    .map(edge => ({
      ...edge,
      source: edge.source,
      target: edge.target,
      parallelIndex: 0,
      parallelCount: 1,
    }));
  const parallelGroups = new Map<string, SimEdge[]>();
  for (const edge of simEdges.value) {
    const endpoints = [String(edge.source), String(edge.target)].sort();
    const key = `${endpoints[0]}\u001f${endpoints[1]}`;
    const group = parallelGroups.get(key) || [];
    group.push(edge);
    parallelGroups.set(key, group);
  }
  for (const group of parallelGroups.values()) {
    group.sort((left, right) => (
      String(left.label || "").localeCompare(String(right.label || ""))
      || String(left.id).localeCompare(String(right.id))
    ));
    group.forEach((edge, index) => {
      edge.parallelIndex = index;
      edge.parallelCount = group.length;
    });
  }
  for (const edge of simEdges.value) {
    nodeMap.get(String(edge.source))!.degree++;
    nodeMap.get(String(edge.target))!.degree++;
  }
  updateImportantLabels();
  if (selectedNodeId.value && !nodeMap.has(selectedNodeId.value)) selectedNodeId.value = null;
  hoveredNodeId.value = null;
  userTransformed = false;
  zoom.value = 1;
  panX = 0;
  panY = 0;

  if (!simNodes.value.length) {
    simulation = null;
    draw();
    return;
  }

  const linkForce = forceLink<SimNode, SimEdge>(simEdges.value)
    .id(node => node.id)
    .distance(edge => 105 + Math.min(35, String(edge.label || "").length * 2))
    .strength(edge => Math.max(0.1, Math.min(0.32, 0.12 + Number(edge.weight || 0) * 0.08)));
  simulation = forceSimulation<SimNode>(simNodes.value)
    .alpha(1)
    .alphaDecay(0.035)
    .velocityDecay(0.36)
    .force("link", linkForce)
    .force("charge", forceManyBody<SimNode>().strength(node => -130 - node.degree * 12).distanceMin(12).distanceMax(720))
    .force("collide", forceCollide<SimNode>().radius(node => nodeRadius(node) + 14).strength(0.95).iterations(2))
    .force("center", forceCenter<SimNode>(width.value / 2, height.value / 2).strength(0.08))
    .force("x", forceX<SimNode>(width.value / 2).strength(0.04))
    .force("y", forceY<SimNode>(height.value / 2).strength(0.04))
    .on("tick", draw)
    .on("end", () => {
      if (autoFitPending && !userTransformed) resetView(false);
      autoFitPending = false;
      draw();
    });

  simulation.stop();
  const warmupTicks = simNodes.value.length > 300 ? 45 : 90;
  for (let index = 0; index < warmupTicks; index++) simulation.tick();
  resetView(false);
  autoFitPending = true;
  simulation.alpha(0.42).restart();
}

function resetLayout() {
  selectedNodeId.value = null;
  hoveredNodeId.value = null;
  tooltip.value = null;
  interaction = null;
  userTransformed = false;
  setData(false);
}

function selectedNeighborhood(): Set<string> {
  const ids = new Set<string>();
  const selected = selectedNodeId.value;
  if (!selected) return ids;
  ids.add(selected);
  for (const edge of simEdges.value) {
    const source = edgeNode(edge.source);
    const target = edgeNode(edge.target);
    if (!source || !target) continue;
    if (source.id === selected) ids.add(target.id);
    if (target.id === selected) ids.add(source.id);
  }
  return ids;
}

interface EdgeGeometry {
  startX: number;
  startY: number;
  controlX: number;
  controlY: number;
  endX: number;
  endY: number;
  tangentX: number;
  tangentY: number;
  normalX: number;
  normalY: number;
  labelX: number;
  labelY: number;
}

function edgeGeometry(edge: SimEdge, source: SimNode, target: SimNode): EdgeGeometry {
  const centerDx = target.x - source.x;
  const centerDy = target.y - source.y;
  const centerDistance = Math.hypot(centerDx, centerDy) || 1;
  const chordX = centerDx / centerDistance;
  const chordY = centerDy / centerDistance;
  const rank = edge.parallelIndex - (edge.parallelCount - 1) / 2;
  const directionSign = source.id <= target.id ? 1 : -1;
  const curveOffset = rank * (34 / zoom.value) * directionSign;
  const normalX = -chordY;
  const normalY = chordX;
  const controlX = (source.x + target.x) / 2 + normalX * curveOffset;
  const controlY = (source.y + target.y) / 2 + normalY * curveOffset;

  const sourceDx = controlX - source.x;
  const sourceDy = controlY - source.y;
  const sourceDistance = Math.hypot(sourceDx, sourceDy) || 1;
  const sourceUx = sourceDx / sourceDistance;
  const sourceUy = sourceDy / sourceDistance;
  const targetDx = target.x - controlX;
  const targetDy = target.y - controlY;
  const targetDistance = Math.hypot(targetDx, targetDy) || 1;
  const tangentX = targetDx / targetDistance;
  const tangentY = targetDy / targetDistance;
  const sourceGap = nodeRadius(source) + 1 / zoom.value;
  const targetGap = nodeRadius(target) + 0.8 / zoom.value;
  const startX = source.x + sourceUx * sourceGap;
  const startY = source.y + sourceUy * sourceGap;
  const endX = target.x - tangentX * targetGap;
  const endY = target.y - tangentY * targetGap;
  return {
    startX,
    startY,
    controlX,
    controlY,
    endX,
    endY,
    tangentX,
    tangentY,
    normalX,
    normalY,
    labelX: (startX + 2 * controlX + endX) / 4,
    labelY: (startY + 2 * controlY + endY) / 4,
  };
}

function drawEdgeLabel(
  context: CanvasRenderingContext2D,
  edge: SimEdge,
  geometry: EdgeGeometry,
  occupied: LabelBounds[],
): LabelBounds | null {
  if (!edge.label) return null;
  const fontSize = 9.5 / zoom.value;
  const label = edge.label.length > 18 ? `${edge.label.slice(0, 17)}…` : edge.label;
  context.save();
  context.font = `600 ${fontSize}px system-ui`;
  const paddingX = 4 / zoom.value;
  const boxHeight = 16 / zoom.value;
  const boxWidth = context.measureText(label).width + paddingX * 2;
  const gap = 3 / zoom.value;
  let bounds: LabelBounds = {
    x: geometry.labelX - boxWidth / 2,
    y: geometry.labelY - boxHeight / 2,
    width: boxWidth,
    height: boxHeight,
  };
  for (const shift of [0, 18, -18, 36, -36]) {
    const candidate = {
      x: geometry.labelX + geometry.normalX * (shift / zoom.value) - boxWidth / 2,
      y: geometry.labelY + geometry.normalY * (shift / zoom.value) - boxHeight / 2,
      width: boxWidth,
      height: boxHeight,
    };
    const overlaps = occupied.some(item => (
      candidate.x < item.x + item.width + gap
      && candidate.x + candidate.width + gap > item.x
      && candidate.y < item.y + item.height + gap
      && candidate.y + candidate.height + gap > item.y
    ));
    if (!overlaps) {
      bounds = candidate;
      break;
    }
  }
  context.fillStyle = "rgba(248,247,241,.96)";
  context.fillRect(bounds.x, bounds.y, boxWidth, boxHeight);
  context.strokeStyle = "rgba(104,117,110,.28)";
  context.lineWidth = 0.65 / zoom.value;
  context.strokeRect(bounds.x, bounds.y, boxWidth, boxHeight);
  context.fillStyle = "#55615b";
  context.textBaseline = "middle";
  context.fillText(label, bounds.x + paddingX, bounds.y + boxHeight / 2);
  context.restore();
  occupied.push(bounds);
  return bounds;
}

interface LabelBounds { x: number; y: number; width: number; height: number }

function drawNodeLabel(
  context: CanvasRenderingContext2D,
  node: SimNode,
  emphasized: boolean,
  occupied: LabelBounds[],
) {
  const radius = nodeRadius(node);
  const fontSize = (emphasized ? 11.5 : 10.5) / zoom.value;
  const text = node.name.length > 24 ? `${node.name.slice(0, 23)}…` : node.name;
  context.save();
  context.font = `${emphasized ? 700 : 600} ${fontSize}px system-ui`;
  const paddingX = 5 / zoom.value;
  const boxHeight = 18 / zoom.value;
  const boxWidth = context.measureText(text).width + paddingX * 2;
  const x = node.x + radius + 6 / zoom.value;
  const y = node.y - boxHeight / 2;
  const bounds = { x, y, width: boxWidth, height: boxHeight };
  const gap = 3 / zoom.value;
  const overlaps = occupied.some(item => (
    x < item.x + item.width + gap
    && x + boxWidth + gap > item.x
    && y < item.y + item.height + gap
    && y + boxHeight + gap > item.y
  ));
  if (overlaps && !emphasized) {
    context.restore();
    return;
  }
  context.fillStyle = emphasized ? "rgba(24,35,31,.94)" : "rgba(248,247,241,.88)";
  context.fillRect(x, y, boxWidth, boxHeight);
  context.fillStyle = emphasized ? "#cfff41" : "#26322d";
  context.textBaseline = "middle";
  context.fillText(text, x + paddingX, y + boxHeight / 2);
  context.restore();
  occupied.push(bounds);
}

function draw() {
  const element = canvas.value;
  if (!element || !hasUsableSize()) return;
  const context = element.getContext("2d");
  if (!context) return;
  const dpr = window.devicePixelRatio || 1;
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, width.value, height.value);
  context.save();
  context.translate(panX, panY);
  context.scale(zoom.value, zoom.value);

  const query = props.search.trim().toLocaleLowerCase();
  const neighborhood = selectedNeighborhood();
  const selected = selectedNodeId.value;
  const hovered = hoveredNodeId.value;
  const visibleEdgeLabels: Array<{ edge: SimEdge; geometry: EdgeGeometry }> = [];
  for (const edge of simEdges.value) {
    const source = edgeNode(edge.source);
    const target = edgeNode(edge.target);
    if (!source || !target) continue;
    const connected = Boolean(selected && (source.id === selected || target.id === selected));
    const hoverConnected = Boolean(hovered && (source.id === hovered || target.id === hovered));
    const dimmed = Boolean(selected && !connected);
    const distance = Math.hypot(target.x - source.x, target.y - source.y) || 1;
    const geometry = edgeGeometry(edge, source, target);
    context.save();
    context.globalAlpha = dimmed ? 0.07 : connected || hoverConnected ? 0.82 : 0.28;
    context.lineWidth = (connected || hoverConnected ? 1.45 : 0.85) / zoom.value;
    context.strokeStyle = connected ? "#395e4d" : "#68756e";
    context.beginPath();
    context.moveTo(geometry.startX, geometry.startY);
    context.quadraticCurveTo(
      geometry.controlX,
      geometry.controlY,
      geometry.endX,
      geometry.endY,
    );
    context.stroke();
    if (!dimmed && distance > 34) {
      const arrowSize = (connected || hoverConnected ? 5.5 : 4) / zoom.value;
      context.fillStyle = connected ? "#395e4d" : "#68756e";
      context.beginPath();
      context.moveTo(geometry.endX, geometry.endY);
      context.lineTo(
        geometry.endX - geometry.tangentX * arrowSize - geometry.tangentY * arrowSize * 0.72,
        geometry.endY - geometry.tangentY * arrowSize + geometry.tangentX * arrowSize * 0.72,
      );
      context.lineTo(
        geometry.endX - geometry.tangentX * arrowSize + geometry.tangentY * arrowSize * 0.72,
        geometry.endY - geometry.tangentY * arrowSize - geometry.tangentX * arrowSize * 0.72,
      );
      context.closePath();
      context.fill();
    }
    context.restore();
    if (connected || hoverConnected) visibleEdgeLabels.push({ edge, geometry });
  }

  const occupiedEdgeLabels: LabelBounds[] = [];
  for (const { edge, geometry } of visibleEdgeLabels) {
    drawEdgeLabel(context, edge, geometry, occupiedEdgeLabels);
  }

  for (const node of simNodes.value) {
    const match = Boolean(query && node.name.toLocaleLowerCase().includes(query));
    const isHovered = hovered === node.id;
    const isSelected = selected === node.id;
    const dimmed = Boolean(selected && !neighborhood.has(node.id));
    const radius = nodeRadius(node);
    context.save();
    context.globalAlpha = dimmed ? 0.18 : 1;
    context.shadowColor = "rgba(24,35,31,.22)";
    context.shadowBlur = 6 / zoom.value;
    context.beginPath();
    context.arc(node.x, node.y, radius, 0, Math.PI * 2);
    context.fillStyle = typeColor(node.type);
    context.fill();
    context.shadowBlur = 0;
    context.lineWidth = 2 / zoom.value;
    context.strokeStyle = "rgba(251,250,246,.95)";
    context.stroke();
    if (match || isHovered || isSelected) {
      context.beginPath();
      context.arc(node.x, node.y, radius + (isSelected ? 5 : 3) / zoom.value, 0, Math.PI * 2);
      context.lineWidth = (isSelected ? 2.4 : 1.6) / zoom.value;
      context.strokeStyle = isSelected ? "#cfff41" : "#18231f";
      context.stroke();
    }
    const glyph = Array.from(node.type || "其他")[0] || "·";
    context.fillStyle = "rgba(255,255,255,.94)";
    context.font = `700 ${Math.max(7, radius * 0.78)}px system-ui`;
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(glyph, node.x, node.y + 0.5);
    context.restore();
  }

  const labelNodes = simNodes.value.filter(node => {
    const match = Boolean(query && node.name.toLocaleLowerCase().includes(query));
    if (selected) return neighborhood.has(node.id);
    return importantLabelIds.value.has(node.id) || match || hovered === node.id;
  });
  labelNodes.sort((a, b) => {
    const aEmphasized = a.id === selected || a.id === hovered || Boolean(query && a.name.toLocaleLowerCase().includes(query));
    const bEmphasized = b.id === selected || b.id === hovered || Boolean(query && b.name.toLocaleLowerCase().includes(query));
    if (aEmphasized !== bEmphasized) return aEmphasized ? 1 : -1;
    return (b.degree * 4 + Number(b.mentions || 0)) - (a.degree * 4 + Number(a.mentions || 0));
  });
  const occupiedLabels: LabelBounds[] = [...occupiedEdgeLabels];
  for (const node of labelNodes) {
    const emphasized = node.id === selected || node.id === hovered || Boolean(query && node.name.toLocaleLowerCase().includes(query));
    drawNodeLabel(context, node, emphasized, occupiedLabels);
  }
  context.restore();
  updateTooltip();
}

function onPointerDown(event: PointerEvent) {
  if (!canvas.value || (event.button !== 0 && event.button !== 2)) return;
  event.preventDefault();
  const point = localPoint(event);
  if (event.button === 2) {
    interaction = {
      kind: "pan",
      pointerId: event.pointerId,
      startX: point.x,
      startY: point.y,
      startPanX: panX,
      startPanY: panY,
    };
    hoveredNodeId.value = null;
    cursor.value = "grabbing";
  } else {
    const node = hitTest(point.x, point.y);
    if (node) {
      const world = screenToWorld(point.x, point.y);
      interaction = {
        kind: "node",
        pointerId: event.pointerId,
        node,
        offsetX: node.x - world.x,
        offsetY: node.y - world.y,
        startX: point.x,
        startY: point.y,
        moved: false,
      };
      selectedNodeId.value = node.id;
      hoveredNodeId.value = node.id;
      node.fx = node.x;
      node.fy = node.y;
      simulation?.alphaTarget(0.18).restart();
      cursor.value = "grabbing";
      draw();
    } else {
      interaction = {
        kind: "node",
        pointerId: event.pointerId,
        node: null,
        offsetX: 0,
        offsetY: 0,
        startX: point.x,
        startY: point.y,
        moved: false,
      };
      selectedNodeId.value = null;
      hoveredNodeId.value = null;
      draw();
    }
  }
  canvas.value.setPointerCapture(event.pointerId);
}

function onPointerMove(event: PointerEvent) {
  const point = localPoint(event);
  if (interaction?.pointerId === event.pointerId) {
    if (interaction.kind === "pan") {
      panX = interaction.startPanX + point.x - interaction.startX;
      panY = interaction.startPanY + point.y - interaction.startY;
      userTransformed = true;
      draw();
      return;
    }
    if (interaction.node) {
      const moved = Math.hypot(point.x - interaction.startX, point.y - interaction.startY) > 3;
      if (moved) {
        interaction.moved = true;
        interaction.node.pinned = true;
        const world = screenToWorld(point.x, point.y);
        interaction.node.x = world.x + interaction.offsetX;
        interaction.node.y = world.y + interaction.offsetY;
        interaction.node.fx = interaction.node.x;
        interaction.node.fy = interaction.node.y;
        interaction.node.vx = 0;
        interaction.node.vy = 0;
        draw();
      }
    }
    return;
  }
  const node = hitTest(point.x, point.y);
  hoveredNodeId.value = node?.id || null;
  cursor.value = node ? "grab" : "default";
  draw();
}

function finishInteraction(event: PointerEvent) {
  if (interaction?.pointerId !== event.pointerId) return;
  if (canvas.value?.hasPointerCapture(event.pointerId)) canvas.value.releasePointerCapture(event.pointerId);
  const point = localPoint(event);
  const node = hitTest(point.x, point.y);
  hoveredNodeId.value = node?.id || null;
  interaction = null;
  simulation?.alphaTarget(0);
  cursor.value = node ? "grab" : "default";
  draw();
}

function onPointerLeave() {
  if (interaction) return;
  hoveredNodeId.value = null;
  cursor.value = "default";
  draw();
}

function onWheel(event: WheelEvent) {
  const point = localPoint(event);
  const delta = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? event.deltaY * 16 : event.deltaY;
  setZoom(zoom.value * Math.exp(-delta * 0.0015), point.x, point.y);
}

watch(dataSignature, async () => {
  layoutPending = true;
  await nextTick();
  resize();
  if (layoutPending) setData();
});
watch(() => props.search, draw);
defineExpose({ resetLayout });
onMounted(async () => {
  await nextTick();
  resizeObserver = new ResizeObserver(resize);
  if (wrapper.value) resizeObserver.observe(wrapper.value);
  resize();
  if (layoutPending) setData();
});
onBeforeUnmount(() => {
  simulation?.stop();
  resizeObserver?.disconnect();
});
</script>

<template>
  <div ref="wrapper" class="graph-canvas-wrap">
    <canvas
      ref="canvas"
      tabindex="0"
      aria-label="知识图谱画布：左键选择或拖动节点，右键拖动画布，滚轮缩放"
      :style="{ cursor }"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="finishInteraction"
      @pointercancel="finishInteraction"
      @pointerleave="onPointerLeave"
      @wheel.prevent="onWheel"
      @contextmenu.prevent
    />
    <div v-if="!nodes.length" class="graph-empty">
      <strong>尚无图谱数据</strong><span>导入文档后点击「构建 GraphRAG」</span>
    </div>
    <div class="graph-canvas-controls" aria-label="图谱缩放控制">
      <button type="button" title="缩小" @click="zoomBy(0.8)">−</button>
      <button type="button" class="zoom-value" title="适配全部节点" @click="resetView()">{{ zoomPercent }}</button>
      <button type="button" title="放大" @click="zoomBy(1.25)">＋</button>
    </div>
    <div class="graph-canvas-help">左键选择/拖动节点 · 右键拖动画布 · 滚轮缩放</div>
    <div class="graph-legend">
      <span v-for="type in types" :key="type" class="legend-item"><i :style="{ background: typeColor(type) }" />{{ type }}</span>
    </div>
    <div v-if="tooltip" class="node-tooltip" :class="{ selected: tooltip.selected }" :style="{ left: `${tooltip.x}px`, top: `${tooltip.y}px` }">
      <small>{{ tooltip.selected ? "SELECTED NODE" : "NODE" }}</small>
      <strong>{{ tooltip.node.name }} · {{ tooltip.node.type }}</strong>
      <span>{{ tooltip.node.description || `${tooltip.node.mentions || 0} 个文本证据` }}</span>
    </div>
  </div>
</template>
