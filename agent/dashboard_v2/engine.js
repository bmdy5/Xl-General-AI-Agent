/**
 * engine.js — 瓦片地图渲染引擎 (P1)
 * 目标: 渲染有纹理地板 + 精确房间轮廓
 * 参考设计目标图: assets/design_target.png
 */

const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d');
ctx.imageSmoothingEnabled = false;

// ── 常量 ──────────────────────────────────────────────
const TILE = 16;      // 逻辑瓦片尺寸 (px)
const SCALE = 3.75;   // 放大倍数 → 16*3.75 = 60px 显示尺寸
const COLS  = 16;     // Canvas 横向瓦片数 (960/60)
const ROWS  = 10.6;   // Canvas 纵向瓦片数 (640/60)

// ── 颜色调色板 (星露谷暖色调) ──────────────────────
const C = {
  // 地板: 暖木 + 石砖 + 地毯
  floorWood:    '#c89048',
  floorWoodD:   '#a07030',
  floorStone:   '#9a9a8a',
  floorStoneD:  '#7a7a6a',
  floorHall:    '#b8a080',
  floorHallD:   '#988060',
  floorCarpet:  '#8b5a3c',
  // 壁: 暖木墙
  wall:         '#6b4a2a',
  wallInner:    '#9a7a50',
  wallTop:      '#4a2a10',
  baseboard:    '#5a3a1a',
  roomLabel:    'rgba(0,0,0,0.4)',
  black:        '#1a1008',
  // 家具
  deskWood:     '#c4a060',
  deskDark:     '#8a6030',
  chairWood:    '#b89850',
  shelfWood:    '#8b6914',
  // 角色
  skin:         '#f4c8a0',
  xlGold:       '#f4d058',
  learnBlue:    '#5b9bd5',
  debateRed:    '#e05555',
  debateGreen:  '#5daa55',
  devilPurple:  '#9b59b6',
  reviewGray:   '#8b8b9b',
  hair:         '#4a3020',
};

// ── 房间定义 (单位: 显示像素) ───────────────────────────
// 参考设计图: 电脑房左上, 图书室上中, XL办公室右上, 杂物室右中, 走廊中, 会议室下
const px = v => v * TILE * SCALE;

const ROOMS = {
  computer: { x: px(0),    y: px(0),    w: px(5.5),  h: px(4.5),  label: '电脑房',   floor: 'wood' },
  library:  { x: px(5.5),  y: px(0),    w: px(4.5),  h: px(5),    label: '图书室',   floor: 'wood' },
  xl:       { x: px(10),   y: px(0),    w: px(4.5),  h: px(4),    label: 'XL办公室', floor: 'carpet' },
  storage:  { x: px(10),   y: px(4),    w: px(4.5),  h: px(2.5),  label: '杂物室',   floor: 'stone' },
  hallway:  { x: px(0),    y: px(4.5),  w: px(10),   h: px(1.5),  label: '走廊',     floor: 'hall' },
  meeting:  { x: px(2.5),  y: px(6),    w: px(12),   h: px(4.67), label: '会议室',   floor: 'carpet'},
};

// ── 瓦片地板绘制 ──────────────────────────────────────
function drawFloor(rx, ry, rw, rh, type) {
  const ts = TILE * SCALE; // 瓦片显示尺寸

  let base, dark, isWood = false, isStone = false, isCarpet = false;
  switch(type) {
    case 'wood':    base = C.floorWood; dark = C.floorWoodD; isWood = true; break;
    case 'carpet':  base = C.floorCarpet; dark = '#6b3a1c'; isCarpet = true; break;
    case 'stone':   base = C.floorStone; dark = C.floorStoneD; isStone = true; break;
    case 'hall':    base = C.floorHall; dark = C.floorHallD; isStone = true; break;
    default:        base = '#888';    dark = '#666';
  }

  // 填充基础色
  ctx.fillStyle = base;
  ctx.fillRect(rx, ry, rw, rh);

  // 木板条纹
  if (isWood) {
    ctx.fillStyle = dark;
    for (let y = ry; y < ry + rh; y += ts * 0.5) {
      ctx.fillRect(rx, y, rw, 2);
    }
    // 竖向分板缝
    ctx.fillStyle = 'rgba(0,0,0,0.12)';
    for (let x = rx; x < rx + rw; x += ts * 1.5) {
      ctx.fillRect(x, ry, 1, rh);
    }
  }

  // 石砖网格
  if (isStone) {
    ctx.strokeStyle = dark;
    ctx.lineWidth = 1.5;
    for (let y = ry; y <= ry + rh; y += ts * 0.75) {
      ctx.beginPath(); ctx.moveTo(rx, y); ctx.lineTo(rx + rw, y); ctx.stroke();
    }
    for (let x = rx; x <= rx + rw; x += ts) {
      ctx.beginPath(); ctx.moveTo(x, ry); ctx.lineTo(x, ry + rh); ctx.stroke();
    }
  }
}

// ── 房间外框 / 墙壁 ───────────────────────────────────
const WALL = 6; // 壁厚 px

function drawRoomShell(r) {
  const { x, y, w, h } = r;
  // 外框阴影
  ctx.fillStyle = '#3a2010';
  ctx.fillRect(x - WALL, y - WALL, w + WALL*2, h + WALL*2);
  // 内墙顶部（略亮）
  ctx.fillStyle = C.wallInner;
  ctx.fillRect(x - WALL + 2, y - WALL + 2, w + WALL*2 - 4, WALL);
  // 圆角感 (4 个角落抠掉)
  ctx.fillStyle = C.black;
  const cr = 4;
  [[x-WALL, y-WALL],[x+w, y-WALL],[x-WALL, y+h],[x+w, y+h]].forEach(([cx,cy]) => {
    ctx.fillRect(cx, cy, cr, cr);
  });
}

// ── 踢脚线 ────────────────────────────────────────────
function drawBaseboard(r) {
  const { x, y, w, h } = r;
  ctx.fillStyle = C.baseboard;
  // 下沿
  ctx.fillRect(x, y + h - 4, w, 4);
}

// ── 房间标签 ──────────────────────────────────────────
function drawLabel(r) {
  const { x, y, w, label } = r;
  ctx.font = '9px monospace';
  ctx.fillStyle = 'rgba(0,0,0,0.5)';
  ctx.fillText(label, x + 9, y + 16);
  ctx.fillStyle = '#f0e8d0';
  ctx.fillText(label, x + 8, y + 15);
}

// ── 全局背景 ──────────────────────────────────────────
function drawBackground() {
  ctx.fillStyle = C.black;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
}

// ── 主渲染函数 ─────────────────────────────────────────
function render() {
  drawBackground();

  // 按顺序渲染：壳 → 地板 → 踢脚线 → 标签
  Object.values(ROOMS).forEach(r => {
    drawRoomShell(r);
    drawFloor(r.x, r.y, r.w, r.h, r.floor);
    drawBaseboard(r);
    drawLabel(r);
  });

  // 渲染家具和角色 (P2/P3 填充)
  if (window.SpriteEngine) window.SpriteEngine.render(ctx);
}

// ── 动画循环 ──────────────────────────────────────────
let lastTime = 0;
function loop(ts) {
  const dt = ts - lastTime;
  lastTime = ts;
  if (window.AgentEngine) window.AgentEngine.update(dt);
  render();
  requestAnimationFrame(loop);
}

// ── 点击检测 ──────────────────────────────────────────
canvas.addEventListener('click', e => {
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
  if (window.AgentEngine) window.AgentEngine.handleClick(mx, my);
});

// ── 启动 ──────────────────────────────────────────────
window.addEventListener('load', () => {
  // 等字体加载
  document.fonts.ready.then(() => {
    requestAnimationFrame(loop);
    if (window.EventBus) window.EventBus.connect();
  });
});

// ── 角色渲染 ────────────────────────────────────────────
// 全局 agent 状态（events.js 更新此对象）
window._agents = {};

function drawAgent(a) {
  if (!a || a.hidden) return;
  const { x, y } = a;
  const c = a.color || C.learnBlue;
  const s = TILE * SCALE * 0.5; // 角色尺寸
  const bounce = Math.sin(Date.now() * 0.005 + (a.id || 'x').charCodeAt(0)) * 1.5;

  // 阴影
  ctx.fillStyle = 'rgba(0,0,0,0.2)';
  ctx.fillRect(x - s/2 + 2, y + s/2 - 1, s, 3);

  // 身体 (方形, Stardew 风格)
  ctx.fillStyle = c;
  ctx.fillRect(x - s/2 + 2, y - s/3 + bounce, s - 4, s * 0.6);
  // 头部 (略圆, 用两个小矩形模拟)
  ctx.fillStyle = C.skin;
  ctx.fillRect(x - s/3 + 1, y - s/2 - 2 + bounce, s * 0.55, s * 0.4);
  // 眼睛
  ctx.fillStyle = '#1a1008';
  ctx.fillRect(x - 2, y - s/3 - 1 + bounce, 2, 2);
  ctx.fillRect(x + 2, y - s/3 - 1 + bounce, 2, 2);
  // 头发
  ctx.fillStyle = C.hair;
  ctx.fillRect(x - s/3, y - s/2 - 4 + bounce, s * 0.55, 2);

  // 名称标签
  if (a.name) {
    ctx.font = '8px monospace';
    ctx.fillStyle = 'rgba(0,0,0,0.5)';
    ctx.fillText(a.name, x - a.name.length * 2 - 1, y - s/2 - 8 + bounce);
    ctx.fillStyle = '#f0e8d0';
    ctx.fillText(a.name, x - a.name.length * 2, y - s/2 - 9 + bounce);
  }

  // 气泡
  if (a.bubble && a.bubbleTimer > 0) {
    const txt = a.bubble.slice(0, 25);
    const bw = Math.min(txt.length * 5 + 12, 160);
    ctx.fillStyle = 'rgba(255,255,255,0.9)';
    ctx.fillRect(x + 6, y - s - 10, bw, 14);
    ctx.strokeStyle = C.wall;
    ctx.lineWidth = 1;
    ctx.strokeRect(x + 6, y - s - 10, bw, 14);
    ctx.fillStyle = '#3a2010';
    ctx.font = '7px monospace';
    ctx.fillText(txt, x + 10, y - s + 1);
  }
}

function drawAllAgents() {
  const agents = window._agents;
  for (const id in agents) {
    drawAgent(agents[id]);
  }
}

function updateAgents() {
  const agents = window._agents;
  const now = Date.now();
  for (const id in agents) {
    const a = agents[id];
    // 移动到目标
    if (a.tx != null && a.ty != null) {
      const dx = a.tx - a.x, dy = a.ty - a.y;
      const dist = Math.sqrt(dx*dx + dy*dy);
      if (dist > 1.5) {
        a.x += dx * 0.06;
        a.y += dy * 0.06;
      } else {
        a.x = a.tx; a.y = a.ty;
      }
    }
    // 气泡计时
    if (a.bubbleTimer > 0) a.bubbleTimer--;
    else a.bubble = '';
  }
}

// ── 主渲染 ────────────────────────────────────────────────
function render() {
  drawBackground();
  Object.values(ROOMS).forEach(r => {
    drawRoomShell(r);
    drawFloor(r.x, r.y, r.w, r.h, r.floor);
    drawBaseboard(r);
    drawLabel(r);
  });
  if (window.SpriteEngine) window.SpriteEngine.render(ctx);
  drawAllAgents();
}

// ── 动画循环 ─────────────────────────────────────────────
function loop(ts) {
  updateAgents();
  render();
  requestAnimationFrame(loop);
}

// ── 启动 (不依赖外部字体) ────────────────────────────────
window.addEventListener('load', () => {
  requestAnimationFrame(loop);
  if (window.EventBus) window.EventBus.connect();
});

// 导出
window.ROOMS = ROOMS;
window.CTX = ctx;
window.TILE = TILE;
window.SCALE = SCALE;
