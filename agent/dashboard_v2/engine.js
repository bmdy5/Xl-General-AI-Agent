/**
 * engine.js — 瓦片地图渲染引擎 (P1)
 * 目标: 渲染有纹理地板 + 精确房间轮廓
 * 参考设计目标图: assets/design_target.png
 */

const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d');
ctx.imageSmoothingEnabled = false;

// ── 常量 (设计图精确参数: 32px瓦片, 960x640) ──────────
const TILE = 32;
const SCALE = 1;
const COLS = 30;
const ROWS = 20;

// ── 调色板 (Mimo分析设计图精确色值) ──────────────────
const C = {
  // 地板
  carpetPurple: '#3d2e4a',
  carpetDark:   '#4a3c5c',
  woodFloor:    '#b8854a',
  woodFloorD:   '#a07535',
  woodWarm:     '#c49a55',
  woodWarmD:    '#b08540',
  stoneFloor:   '#7a7a82',
  stoneFloorD:  '#5a5a6a',
  // 墙壁
  wallPurple:   '#5a5a6a',
  wallDark:     '#5a4030',
  wallWarm:     '#8a7a5a',
  wallLight:    '#d4c4a8',
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
  // 装饰
  plant:        '#2a6b2a',
  plantLight:   '#3a8a3a',
};

// ── 房间定义 (Mimo分析设计图精确坐标) ──────────────────
const ROOMS = {
  computer:  { x:15,  y:20,  w:200, h:200, label:'电脑房',   floor:'purple', wall:C.wallPurple },
  manager:   { x:215, y:20,  w:165, h:190, label:'经理室',   floor:'wood',   wall:C.wallDark },
  reception: { x:390, y:15,  w:170, h:140, label:'资料室',   floor:'woodWarm', wall:C.wallWarm },
  mimo:      { x:570, y:15,  w:170, h:140, label:'Mimo视觉', floor:'stone',wall:C.wallPurple },
  storage:   { x:20,  y:240, w:130, h:150, label:'储藏室',   floor:'purple', wall:C.wallPurple },
  hallway:   { x:155, y:200, w:430, h:170, label:'走廊',     floor:'stone', wall:C.wallWarm },
  meeting:   { x:255, y:390, w:655, h:235, label:'会议室',   floor:'purple', wall:C.wallLight },
  xlOffice:  { x:580, y:215, w:160, h:160, label:'XL办公室', floor:'wood',   wall:C.wallDark },
};

// ── 瓦片地板绘制 ──────────────────────────────────────
function drawFloor(rx, ry, rw, rh, type) {
  const ts = TILE * SCALE; // 瓦片显示尺寸

  let base, dark, isWood = false, isStone = false;
  switch(type) {
    case 'wood':    base = C.woodFloor; dark = C.woodFloorD; isWood = true; break;
    case 'woodWarm':base = C.woodWarm; dark = C.woodWarmD; isWood = true; break;
    case 'purple':  base = C.carpetPurple; dark = C.carpetDark; break;
    case 'stone':   base = C.stoneFloor; dark = C.stoneFloorD; isStone = true; break;
    default:        base = '#888';    dark = '#666';
  }

  ctx.fillStyle = base; ctx.fillRect(rx, ry, rw, rh);
  if (isWood) {
    ctx.fillStyle = dark;
    for (let y = ry; y < ry+rh; y += 16) ctx.fillRect(rx, y, rw, 2);
    ctx.fillStyle = 'rgba(0,0,0,0.1)';
    for (let x = rx; x < rx+rw; x += 48) ctx.fillRect(x, ry, 1, rh);
  }
  if (isStone) {
    ctx.strokeStyle = dark; ctx.lineWidth = 1;
    for (let y = ry; y <= ry+rh; y += 24) { ctx.beginPath(); ctx.moveTo(rx,y); ctx.lineTo(rx+rw,y); ctx.stroke(); }
    for (let x = rx; x <= rx+rw; x += 32) { ctx.beginPath(); ctx.moveTo(x,ry); ctx.lineTo(x,ry+rh); ctx.stroke(); }
  }
}

// ── 房间外框 / 墙壁 ───────────────────────────────────
const WALL = 6; // 壁厚 px

function drawRoomShell(r) {
  const { x, y, w, h, wall } = r;
  ctx.fillStyle = wall || C.wallDark;
  ctx.fillRect(x-WALL, y-WALL, w+WALL*2, h+WALL*2);
  ctx.fillStyle = 'rgba(255,255,255,0.1)';
  ctx.fillRect(x-WALL+2, y-WALL+2, w+WALL*2-4, WALL);
}

function drawBackground() {
  ctx.fillStyle = '#1a1010';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
}

function drawBaseboard(r) {
  const { x, y, w, h } = r;
  ctx.fillStyle = 'rgba(0,0,0,0.3)';
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

// ── 主渲染 ────────────────────────────────────────────
function render() {
  // 测试: 画一个显眼的红色大矩形
  ctx.fillStyle = '#ff0000';
  ctx.fillRect(0, 0, 960, 640);
  ctx.fillStyle = '#00ff00';
  ctx.fillRect(50, 50, 860, 540);
  ctx.fillStyle = '#0000ff';
  ctx.fillRect(100, 100, 760, 440);
  ctx.fillStyle = '#ffffff';
  ctx.font = '30px monospace';
  ctx.fillText('TEST - If you see this, Canvas works', 150, 200);

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

function loop(ts) {
  updateAgents();
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

// ── 启动 ────────────────────────────────────────────────
window.addEventListener('load', () => {
  requestAnimationFrame(loop);
  if (window.EventBus) window.EventBus.connect();
});

// 导出
window.ROOMS = ROOMS;
window.CTX = ctx;
window.TILE = TILE;
window.SCALE = SCALE;
