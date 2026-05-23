/**
 * events.js — Agent 状态机 + 点击交互 + SSE 连接 (P3/P4/P5)
 */

const T = TILE * SCALE;

// ── Agent 定义 ────────────────────────────────────────
const AGENT_DEFS = {
  'xl':        { name: 'XL Agent',   role: 'DIRECTOR',  color: '#f4d058', cape: '#e04b4b', room: 'xl',       mem: 42, hp: 90 },
  'learner-1': { name: 'Learner-01', role: 'RESEARCH',  color: '#4ba4e0', cape: null,       room: 'computer', mem: 12, hp: 65 },
  'debater-a': { name: 'Aggressive', role: 'DEBATE',    color: '#e04b4b', cape: null,       room: 'meeting',  mem: 8,  hp: 80 },
  'debater-b': { name: 'Conservative',role: 'DEBATE',   color: '#4be05a', cape: null,       room: 'meeting',  mem: 8,  hp: 75 },
  'reviewer':  { name: 'Reviewer',   role: 'REVIEW',    color: '#8b8b9b', cape: null,       room: 'meeting',  mem: 20, hp: 70 },
};

// ── Agent 实例状态 ────────────────────────────────────
const agents = {};

function spawnAgent(id, def) {
  const spawnPos = getRoomCenter(def.room);
  agents[id] = {
    ...def,
    id,
    x: spawnPos.x + (Math.random() - 0.5) * T,
    y: spawnPos.y + (Math.random() - 0.5) * T,
    tx: spawnPos.x, ty: spawnPos.y,
    walkFrame: 0, walkTick: 0,
    bubble: null, bubbleTick: 0,
    state: 'idle',  // idle | walk | work | debate
    selected: false,
  };
}

function getRoomCenter(roomKey) {
  const r = window.ROOMS[roomKey] || window.ROOMS.hallway;
  return { x: r.x + r.w / 2, y: r.y + r.h / 2 };
}

// ── 初始化所有 Agent ──────────────────────────────────
Object.entries(AGENT_DEFS).forEach(([id, def]) => spawnAgent(id, def));

// ── 角色精灵绘制（fillRect 像素小人）─────────────────
function drawAgent(ctx, a) {
  const x = Math.round(a.x);
  const y = Math.round(a.y);
  const isWalking = Math.abs(a.x - a.tx) > 1 || Math.abs(a.y - a.ty) > 1;
  const bobY = isWalking ? Math.sin(a.walkFrame * 0.5) * 2 : 0;

  // 投影
  ctx.fillStyle = 'rgba(0,0,0,0.2)';
  ctx.beginPath();
  ctx.ellipse(x + 8, y + 30, 9, 4, 0, 0, Math.PI*2);
  ctx.fill();

  // 披风（仅 XL）
  if (a.cape) {
    ctx.fillStyle = a.cape;
    ctx.fillRect(x + 3, y + 10 + bobY, 10, 14);
  }

  // 身体
  ctx.fillStyle = a.color;
  ctx.fillRect(x + 4, y + 12 + bobY, 8, 10);

  // 头部
  ctx.fillStyle = a.color;
  ctx.fillRect(x + 3, y + 2 + bobY, 10, 10);
  // 头部阴影（深色）
  ctx.fillStyle = shadeColor(a.color, -30);
  ctx.fillRect(x + 3, y + 10 + bobY, 10, 2);

  // 眼睛
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(x + 4, y + 5 + bobY, 3, 3);
  ctx.fillRect(x + 9, y + 5 + bobY, 3, 3);
  ctx.fillStyle = '#1a1a2a';
  ctx.fillRect(x + 5, y + 6 + bobY, 2, 2);
  ctx.fillRect(x + 10, y + 6 + bobY, 2, 2);

  // 腿（行走动画）
  const legOff = isWalking ? Math.sin(a.walkFrame * 0.4) * 3 : 0;
  ctx.fillStyle = shadeColor(a.color, -50);
  ctx.fillRect(x + 4, y + 22 + bobY, 4, 7 + legOff);
  ctx.fillRect(x + 8, y + 22 + bobY, 4, 7 - legOff);

  // 选中光圈
  if (a.selected) {
    ctx.strokeStyle = '#f4d058';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.ellipse(x + 8, y + 30, 12, 5, 0, 0, Math.PI*2);
    ctx.stroke();
  }

  // 气泡
  if (a.bubble) {
    ctx.fillStyle = 'rgba(255,255,255,0.9)';
    ctx.fillRect(x - 5, y - 20 + bobY, 28, 16);
    ctx.fillStyle = '#333';
    ctx.font = '10px serif';
    ctx.fillText(a.bubble, x - 2, y - 8 + bobY);
    // 气泡小尾巴
    ctx.fillStyle = 'rgba(255,255,255,0.9)';
    ctx.fillRect(x + 6, y - 5 + bobY, 5, 5);
  }
}

function shadeColor(hex, pct) {
  const num = parseInt(hex.slice(1), 16);
  const r = Math.min(255, Math.max(0, (num >> 16) + pct));
  const g = Math.min(255, Math.max(0, ((num >> 8) & 0xff) + pct));
  const b = Math.min(255, Math.max(0, (num & 0xff) + pct));
  return `rgb(${r},${g},${b})`;
}

// ── 更新循环 ──────────────────────────────────────────
window.AgentEngine = {
  update(dt) {
    Object.values(agents).forEach(a => {
      // 移动插值
      const dx = a.tx - a.x, dy = a.ty - a.y;
      if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) {
        a.x += dx * 0.07;
        a.y += dy * 0.07;
        a.state = 'walk';
      } else {
        a.state = 'idle';
      }
      // 行走帧
      a.walkTick += dt;
      if (a.walkTick > 120) { a.walkFrame++; a.walkTick = 0; }
      // 气泡倒计时
      if (a.bubbleTick > 0) {
        a.bubbleTick -= dt;
        if (a.bubbleTick <= 0) a.bubble = null;
      }
    });
  },

  render(ctx) {
    // 先渲染非选中，再渲染选中（确保选中角色在顶层）
    Object.values(agents).filter(a => !a.selected).forEach(a => drawAgent(ctx, a));
    Object.values(agents).filter(a => a.selected).forEach(a => drawAgent(ctx, a));
  },

  handleClick(mx, my) {
    let hit = null;
    Object.values(agents).forEach(a => {
      const dx = mx - (a.x + 8), dy = my - (a.y + 15);
      if (Math.abs(dx) < 16 && Math.abs(dy) < 18) hit = a;
    });

    // 取消所有选中
    Object.values(agents).forEach(a => a.selected = false);

    if (hit) {
      hit.selected = true;
      showPanel(hit);
    } else {
      hidePanel();
    }
  },

  // 对外接口：事件驱动 Agent 移动
  moveAgent(id, roomKey, bubble = null) {
    if (!agents[id]) spawnAgent(id, AGENT_DEFS[id] || { name: id, role: '?', color: '#888', room: roomKey, mem: 0, hp: 50 });
    const pos = getRoomCenter(roomKey);
    const jitter = T * 0.5;
    agents[id].tx = pos.x + (Math.random() - 0.5) * jitter;
    agents[id].ty = pos.y + (Math.random() - 0.5) * jitter;
    if (bubble) {
      agents[id].bubble = bubble;
      agents[id].bubbleTick = 3000;
    }
  },

  setBubble(id, bubble, ms = 3000) {
    if (agents[id]) { agents[id].bubble = bubble; agents[id].bubbleTick = ms; }
  },
};

// ── Agent 信息面板 ────────────────────────────────────
function showPanel(a) {
  const panel = document.getElementById('agent-panel');
  panel.classList.add('visible');
  document.getElementById('panel-name').textContent = a.name;
  document.getElementById('panel-role').textContent = a.role;
  document.getElementById('panel-hp').style.width = a.hp + '%';
  document.getElementById('panel-mem').textContent = a.mem + ' KIs';
  document.getElementById('panel-status').textContent = getStatusText(a.state);
  document.getElementById('panel-icon').textContent = getStatusIcon(a);
}

function hidePanel() {
  document.getElementById('agent-panel').classList.remove('visible');
}

function getStatusText(state) {
  const map = { idle: 'Standing by...', walk: 'Moving to target...', work: 'Processing...', debate: 'In debate session' };
  return map[state] || 'Unknown';
}

function getStatusIcon(a) {
  if (a.bubble) return a.bubble;
  return a.role === 'RESEARCH' ? '🔍' : a.role === 'DEBATE' ? '💬' : a.role === 'REVIEW' ? '📋' : '⭐';
}

// ── 事件日志 ──────────────────────────────────────────
function addLog(msg, type = 'info') {
  const el = document.getElementById('log-entries');
  const div = document.createElement('div');
  div.className = `log-entry ${type}`;
  const time = new Date().toLocaleTimeString('zh', { hour12: false });
  div.textContent = `[${time}] ${msg}`;
  el.prepend(div);
  // 最多保留 50 条
  while (el.children.length > 50) el.removeChild(el.lastChild);
}

// ── SSE 连接 ──────────────────────────────────────────
window.EventBus = {
  connect() {
    const src = new EventSource('/events');
    src.onmessage = e => {
      try {
        const d = JSON.parse(e.data);
        this.handle(d);
      } catch (_) {}
    };
    src.onerror = () => addLog('SSE 连接断开，等待重连...', 'warn');
    addLog('Dashboard v2 已启动', 'action');

    // 演示模式：如果没有真实 SSE，自动模拟 Agent 活动
    this.startDemo();
  },

  handle(d) {
    const { agent, event, name, action } = d;
    const roomMap = {
      'search_start': 'computer', 'web_search': 'computer',
      'read_local': 'library',    'fetch_article': 'library',
      'enter_debate': 'meeting',  'critique': 'meeting',
      'rebut': 'meeting',         'score': 'meeting',
      'save_memory': 'xl',        'learn_done': 'hallway',
      'periodic_nudge': 'xl',
    };
    const bubbleMap = {
      'search_start': '🌐', 'web_search': '🔍',
      'read_local': '📖',   'fetch_article': '📄',
      'enter_debate': '💬', 'critique': '❓',
      'rebut': '💡',        'score': `⭐${action}`,
      'save_memory': '💾',  'periodic_nudge': '💡',
    };
    const room = roomMap[event];
    if (room && agent) {
      window.AgentEngine.moveAgent(agent, room, bubbleMap[event] || null);
      addLog(`${agent}: ${event}${name ? ' → ' + name : ''}`, 'action');
    }
  },

  // 演示模式（无真实 SSE 时自动展示效果）
  startDemo() {
    const demoEvents = [
      () => { window.AgentEngine.moveAgent('learner-1', 'computer', '🌐'); addLog('learner-1: web_search → Python', 'action'); },
      () => { window.AgentEngine.moveAgent('learner-1', 'library', '📖'); addLog('learner-1: read_local', 'action'); },
      () => { window.AgentEngine.moveAgent('debater-a', 'meeting', '💬'); addLog('debater-a: enter_debate', 'action'); },
      () => { window.AgentEngine.moveAgent('debater-b', 'meeting', '💬'); addLog('debater-b: enter_debate', 'action'); },
      () => { window.AgentEngine.setBubble('debater-a', '❓'); addLog('debater-a: critique', 'action'); },
      () => { window.AgentEngine.setBubble('debater-b', '💡'); addLog('debater-b: rebut', 'action'); },
      () => { window.AgentEngine.moveAgent('learner-1', 'xl', '💾'); addLog('learner-1: save_memory', 'action'); },
      () => { window.AgentEngine.setBubble('xl', '💡'); addLog('xl: periodic_nudge', 'warn'); },
      () => { window.AgentEngine.moveAgent('learner-1', 'hallway', null); addLog('learner-1: learn_done → idle', 'info'); },
    ];
    let i = 0;
    const runNext = () => {
      demoEvents[i % demoEvents.length]();
      i++;
      setTimeout(runNext, 2500 + Math.random() * 1500);
    };
    setTimeout(runNext, 1500);
  },
};

// ── 覆盖 SpriteEngine.render 以包含 Agent 绘制 ────────
const _origRender = window.SpriteEngine.render.bind(window.SpriteEngine);
window.SpriteEngine.render = function(ctx) {
  _origRender(ctx);
  window.AgentEngine.render(ctx);
};
