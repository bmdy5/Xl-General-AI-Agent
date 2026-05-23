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

// ── 颜色调色板 (与设计目标图对齐) ──────────────────────
const C = {
  // 地板
  floorWood:    '#c8934a',
  floorWoodD:   '#a07038',
  floorPurple:  '#7856a0',
  floorPurpleD: '#5a3a80',
  floorStone:   '#8a8a9a',
  floorStoneD:  '#6a6a7a',
  floorHall:    '#a09080',
  floorHallD:   '#807060',
  // 壁
  wall:         '#5a3a1a',
  wallInner:    '#8a6040',
  wallTop:      '#3a2010',
  // 踢脚线
  baseboard:    '#4a2a0a',
  // 房间标题
  roomLabel:    'rgba(0,0,0,0.45)',
  // 黑色区域（房间之外）
  black:        '#000000',
};

// ── 房间定义 (单位: 显示像素) ───────────────────────────
// 参考设计图: 电脑房左上, 图书室上中, XL办公室右上, 杂物室右中, 走廊中, 会议室下
const px = v => v * TILE * SCALE;

const ROOMS = {
  computer: { x: px(0),    y: px(0),    w: px(5.5),  h: px(4.5),  label: '⌨ 电脑房',   floor: 'purple' },
  library:  { x: px(5.5),  y: px(0),    w: px(4.5),  h: px(5),    label: '📚 图书室',   floor: 'wood'   },
  xl:       { x: px(10),   y: px(0),    w: px(4.5),  h: px(4),    label: '👑 XL办公室', floor: 'carpet' },
  storage:  { x: px(10),   y: px(4),    w: px(4.5),  h: px(2.5),  label: '📦 杂物室',   floor: 'stone'  },
  hallway:  { x: px(0),    y: px(4.5),  w: px(10),   h: px(1.5),  label: '🚶 走廊',     floor: 'hall'   },
  meeting:  { x: px(2.5),  y: px(6),    w: px(12),   h: px(4.67), label: '🗣 会议室',   floor: 'purple2'},
};

// ── 瓦片地板绘制 ──────────────────────────────────────
function drawFloor(rx, ry, rw, rh, type) {
  const ts = TILE * SCALE; // 瓦片显示尺寸

  let base, dark, isWood = false, isStone = false;
  switch(type) {
    case 'wood':    base = '#c8934a'; dark = '#a07038'; isWood = true;  break;
    case 'purple':  base = '#6a4a8a'; dark = '#523870'; break;
    case 'purple2': base = '#7050a0'; dark = '#5a3888'; break;
    case 'carpet':  base = '#9a7a50'; dark = '#7a5a30'; break;
    case 'stone':   base = '#8a8a9a'; dark = '#6a6a7a'; isStone = true; break;
    case 'hall':    base = '#909090'; dark = '#707070'; isStone = true; break;
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
  ctx.font = '9px "Press Start 2P", monospace';
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

// 导出给其他模块使用
window.ROOMS = ROOMS;
window.CTX = ctx;
window.TILE = TILE;
window.SCALE = SCALE;
