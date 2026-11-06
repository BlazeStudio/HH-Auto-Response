// Едва заметные грани по краям страницы. Вершины сетки медленно дышат, а свет скользит по ней,
// поэтому грани то проступают, то гаснут. Рисуем только на широких экранах и не чаще 30 раз в секунду.

import { reducedMotion } from './motion';

interface Vertex {
  x0: number;
  y0: number;
  z0: number;
  phase: number;
  speed: number;
  x: number;
  y: number;
  z: number;
}

interface Facet {
  a: Vertex;
  b: Vertex;
  c: Vertex;
  color: string;
  fade: number;
}

interface Mesh {
  canvas: HTMLCanvasElement;
  context: CanvasRenderingContext2D;
  side: string;
  seed: number;
  width: number;
  height: number;
  vertices: Vertex[];
  facets: Facet[];
}

const CELL = 138;
const FRAME_MS = 1000 / 30;
const DRIFT = 16;
const LIFT = 34;
const FILL = 0.06;
const EDGE = 0.055;
const SKY = [56, 189, 248];
const VIOLET = [139, 92, 246];
const WIDE_SCREEN = '(min-width: 64rem)';

function seededRandom(seed: number) {
  let state = seed;
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let mixed = Math.imul(state ^ (state >>> 15), 1 | state);
    mixed = (mixed + Math.imul(mixed ^ (mixed >>> 7), 61 | mixed)) ^ mixed;
    return ((mixed ^ (mixed >>> 14)) >>> 0) / 4294967296;
  };
}

function buildMesh(mesh: Mesh) {
  const random = seededRandom(mesh.seed);
  const columns = Math.ceil(mesh.width / CELL) + 2;
  const rows = Math.ceil(mesh.height / CELL) + 2;
  const vertices: Vertex[] = [];

  for (let row = 0; row <= rows; row += 1) {
    for (let column = 0; column <= columns; column += 1) {
      vertices.push({
        x0: (column - 0.5 + (random() - 0.5) * 0.85) * CELL,
        y0: (row - 0.5 + (random() - 0.5) * 0.85) * CELL,
        z0: random() * 90,
        phase: random() * Math.PI * 2,
        speed: 0.6 + random() * 0.8,
        x: 0,
        y: 0,
        z: 0,
      });
    }
  }

  const at = (row: number, column: number) => vertices[row * (columns + 1) + column];
  const facets: Facet[] = [];

  const addFacet = (a: Vertex, b: Vertex, c: Vertex) => {
    const centerX = (a.x0 + b.x0 + c.x0) / 3;
    const centerY = (a.y0 + b.y0 + c.y0) / 3;
    const fromEdge = mesh.side === 'left' ? centerX : mesh.width - centerX;
    const reach = Math.min(1, Math.max(0, 1 - fromEdge / mesh.width));
    const mix = Math.min(1, Math.max(0, centerY / mesh.height + (random() - 0.5) * 0.35));
    const channel = (index: number) => Math.round(SKY[index] + (VIOLET[index] - SKY[index]) * mix);
    facets.push({
      a,
      b,
      c,
      color: `rgb(${channel(0)} ${channel(1)} ${channel(2)})`,
      fade: reach * reach * (3 - 2 * reach),
    });
  };

  for (let row = 0; row < rows; row += 1) {
    for (let column = 0; column < columns; column += 1) {
      const topLeft = at(row, column);
      const topRight = at(row, column + 1);
      const bottomLeft = at(row + 1, column);
      const bottomRight = at(row + 1, column + 1);
      if (random() > 0.5) {
        addFacet(topLeft, topRight, bottomRight);
        addFacet(topLeft, bottomRight, bottomLeft);
      } else {
        addFacet(topLeft, topRight, bottomLeft);
        addFacet(topRight, bottomRight, bottomLeft);
      }
    }
  }

  mesh.vertices = vertices;
  mesh.facets = facets;
}

function draw(mesh: Mesh, time: number) {
  const { context, vertices, facets, width, height, seed } = mesh;

  for (const vertex of vertices) {
    const pace = time * vertex.speed;
    vertex.x = vertex.x0 + Math.sin(pace * 0.17 + vertex.phase) * DRIFT;
    vertex.y = vertex.y0 + Math.cos(pace * 0.13 + vertex.phase * 1.7) * DRIFT;
    vertex.z = vertex.z0 + Math.sin(pace * 0.11 + vertex.phase * 2.3) * LIFT;
  }

  // Свет ходит по кругу сам и чуть сдвигается при прокрутке страницы
  const angle = time * 0.06 + window.scrollY * 0.0012 + seed;
  const lightX = Math.cos(angle) * 0.72;
  const lightY = Math.sin(angle) * 0.72;
  const lightZ = 0.69;

  context.clearRect(0, 0, width, height);
  context.strokeStyle = 'rgb(165 190 235)';
  context.lineWidth = 1;

  for (const { a, b, c, color, fade } of facets) {
    const ux = b.x - a.x;
    const uy = b.y - a.y;
    const uz = b.z - a.z;
    const vx = c.x - a.x;
    const vy = c.y - a.y;
    const vz = c.z - a.z;
    const nx = uy * vz - uz * vy;
    const ny = uz * vx - ux * vz;
    const nz = ux * vy - uy * vx;
    const length = Math.hypot(nx, ny, nz) || 1;
    const facing = Math.abs((nx * lightX + ny * lightY + nz * lightZ) / length);
    const glow = Math.max(0, (facing - 0.62) / 0.38) ** 2;

    context.beginPath();
    context.moveTo(a.x, a.y);
    context.lineTo(b.x, b.y);
    context.lineTo(c.x, c.y);
    context.closePath();

    context.globalAlpha = fade * glow * FILL;
    context.fillStyle = color;
    context.fill();

    context.globalAlpha = fade * (0.018 + glow * EDGE);
    context.stroke();
  }
}

export function initFacets() {
  const canvases = [...document.querySelectorAll<HTMLCanvasElement>('[data-facets]')];
  if (!canvases.length) return;

  const meshes: Mesh[] = [];
  canvases.forEach((canvas, index) => {
    const context = canvas.getContext('2d');
    if (!context) return;
    meshes.push({
      canvas,
      context,
      side: canvas.dataset.facets ?? 'left',
      seed: 11 + index * 37,
      width: 0,
      height: 0,
      vertices: [],
      facets: [],
    });
  });

  const wide = window.matchMedia(WIDE_SCREEN);
  const still = reducedMotion();
  // Время берём от часов, чтобы при переходе на другую страницу грани не прыгали
  const now = () => (Date.now() / 1000) % 100000;
  let frame = 0;
  let lastDraw = 0;

  const resize = (mesh: Mesh) => {
    const width = mesh.canvas.clientWidth;
    const height = mesh.canvas.clientHeight;
    if (!width || !height) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
    mesh.canvas.width = Math.round(width * ratio);
    mesh.canvas.height = Math.round(height * ratio);
    mesh.context.setTransform(ratio, 0, 0, ratio, 0, 0);
    mesh.width = width;
    mesh.height = height;
    buildMesh(mesh);
    draw(mesh, now());
    mesh.canvas.dataset.ready = '';
  };

  const loop = (stamp: number) => {
    frame = requestAnimationFrame(loop);
    if (stamp - lastDraw < FRAME_MS) return;
    lastDraw = stamp;
    const time = now();
    meshes.forEach((mesh) => {
      if (mesh.width) draw(mesh, time);
    });
  };

  const sync = () => {
    cancelAnimationFrame(frame);
    if (wide.matches && !still) frame = requestAnimationFrame(loop);
  };

  const observer = new ResizeObserver((entries) => {
    entries.forEach((entry) => {
      const mesh = meshes.find((item) => item.canvas === entry.target);
      if (mesh) resize(mesh);
    });
  });
  meshes.forEach((mesh) => observer.observe(mesh.canvas));

  wide.addEventListener('change', sync);
  sync();
}
