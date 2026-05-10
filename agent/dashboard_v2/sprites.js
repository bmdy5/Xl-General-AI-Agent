/**
 * sprites.js — 精灵 & 家具渲染 (P2 框架，P1 先用纯 Canvas 绘制)
 * 使用 fillRect 绘制家具占位，P2 升级为 drawImage
 */

const S = SCALE;
const T = TILE * S; // 一格显示尺寸

// ── 家具绘制工具 ──────────────────────────────────────
function rect(x, y, w, h, color, shadow = true) {
  const ctx = window.CTX;
  if (shadow) {
    ctx.fillStyle = 'rgba(0,0,0,0.3)';
    ctx.fillRect(x + 3, y + 4, w, h);
  }
  ctx.fillStyle = color;
  ctx.fillRect(x, y, w, h);
}

function pixel(x, y, s, color) {
  window.CTX.fillStyle = color;
  window.CTX.fillRect(x, y, s, s);
}

// ── 家具定义 ──────────────────────────────────────────
// 电脑桌 (desk + monitor + chair)
function drawDesk(x, y) {
  const ctx = window.CTX;
  // 桌面阴影
  ctx.fillStyle = 'rgba(0,0,0,0.25)';
  ctx.fillRect(x+3, y+4, T*1.4, T*0.9);
  // 桌面
  ctx.fillStyle = '#c4a060';
  ctx.fillRect(x, y, T*1.4, T*0.85);
  // 桌面深色边缘（底部）
  ctx.fillStyle = '#8a6030';
  ctx.fillRect(x, y + T*0.75, T*1.4, T*0.1);
  // 桌腿
  ctx.fillStyle = '#7a5020';
  ctx.fillRect(x + 2, y + T*0.85, 5, T*0.2);
  ctx.fillRect(x + T*1.4 - 7, y + T*0.85, 5, T*0.2);
  // 显示器（蓝屏发光）
  ctx.fillStyle = '#0a2040';
  ctx.fillRect(x + T*0.3, y - T*0.6, T*0.7, T*0.55);
  ctx.fillStyle = '#4ba4e0';
  ctx.fillRect(x + T*0.35, y - T*0.55, T*0.6, T*0.42);
  // 屏幕光晕
  ctx.fillStyle = 'rgba(75,164,224,0.15)';
  ctx.fillRect(x + T*0.2, y - T*0.7, T*0.9, T*0.7);
  // 键盘
  ctx.fillStyle = '#888';
  ctx.fillRect(x + T*0.15, y + T*0.1, T*0.8, T*0.2);
}

function drawChair(x, y) {
  const ctx = window.CTX;
  // 椅背阴影
  ctx.fillStyle = 'rgba(0,0,0,0.2)';
  ctx.fillRect(x+3, y+3, T*0.7, T*0.6);
  // 椅背
  ctx.fillStyle = '#3a3a3a';
  ctx.fillRect(x, y, T*0.65, T*0.5);
  // 椅面
  ctx.fillStyle = '#555';
  ctx.fillRect(x - T*0.05, y + T*0.45, T*0.75, T*0.3);
  // 椅腿
  ctx.fillStyle = '#222';
  ctx.fillRect(x + T*0.05, y + T*0.75, 4, T*0.15);
  ctx.fillRect(x + T*0.55, y + T*0.75, 4, T*0.15);
}

function drawBookshelf(x, y, h = 1.8) {
  const ctx = window.CTX;
  // 框架
  ctx.fillStyle = '#6a4010';
  ctx.fillRect(x, y, T*1.2, T*h);
  // 书脊（随机颜色）
  const bookColors = ['#e04b4b','#4ba4e0','#4be05a','#f4d058','#a04be0','#e07a4b','#4be0d0'];
  let bx = x + 4, by = y + 4;
  for (let row = 0; row < Math.floor(h * 2); row++) {
    bx = x + 4;
    for (let i = 0; i < 6; i++) {
      const bw = 8 + Math.floor((x + y + i + row) % 3) * 3;
      ctx.fillStyle = bookColors[(x + i + row * 3) % bookColors.length];
      ctx.fillRect(bx, by, bw - 2, T*0.38);
      // 书脊高亮
      ctx.fillStyle = 'rgba(255,255,255,0.2)';
      ctx.fillRect(bx, by, 2, T*0.38);
      bx += bw;
      if (bx > x + T*1.15) break;
    }
    by += T * 0.42;
    if (by > y + T * h - 8) break;
  }
  // 框架边缘
  ctx.strokeStyle = '#3a2008';
  ctx.lineWidth = 2;
  ctx.strokeRect(x, y, T*1.2, T*h);
}

function drawPlant(x, y) {
  const ctx = window.CTX;
  // 花盆
  ctx.fillStyle = '#c87040';
  ctx.fillRect(x + T*0.1, y + T*0.55, T*0.5, T*0.4);
  ctx.fillStyle = '#a05030';
  ctx.fillRect(x + T*0.05, y + T*0.5, T*0.6, T*0.08);
  // 叶子
  const leaves = [
    [x, y, '#2a8a2a'], [x+T*0.25, y-T*0.2, '#38aa38'],
    [x+T*0.4, y+T*0.1, '#2a8a2a'], [x-T*0.05, y+T*0.15, '#1a7a1a'],
  ];
  leaves.forEach(([lx, ly, lc]) => {
    ctx.fillStyle = lc;
    ctx.beginPath();
    ctx.ellipse(lx + T*0.15, ly + T*0.15, T*0.2, T*0.15, Math.PI/4, 0, Math.PI*2);
    ctx.fill();
  });
}

function drawConferenceTable(x, y) {
  const ctx = window.CTX;
  // 阴影
  ctx.fillStyle = 'rgba(0,0,0,0.3)';
  ctx.beginPath();
  ctx.ellipse(x + T*2 + 5, y + T*1.5 + 6, T*2, T*1.5, 0, 0, Math.PI*2);
  ctx.fill();
  // 桌面
  ctx.fillStyle = '#8a5020';
  ctx.beginPath();
  ctx.ellipse(x + T*2, y + T*1.5, T*2, T*1.5, 0, 0, Math.PI*2);
  ctx.fill();
  // 桌面高光
  ctx.fillStyle = '#b07040';
  ctx.beginPath();
  ctx.ellipse(x + T*1.8, y + T*1.3, T*1.6, T*1.1, -0.2, 0, Math.PI*2);
  ctx.fill();
  // 桌面纹理
  ctx.fillStyle = 'rgba(0,0,0,0.1)';
  ctx.beginPath();
  ctx.ellipse(x + T*2, y + T*1.5, T*2, T*1.5, 0, 0, Math.PI*2);
  ctx.fill();
}

function drawWaterCooler(x, y) {
  const ctx = window.CTX;
  // 主体
  ctx.fillStyle = '#d0d8e0';
  ctx.fillRect(x, y + T*0.4, T*0.55, T*0.8);
  // 水桶（蓝色）
  ctx.fillStyle = '#5090d0';
  ctx.beginPath();
  ctx.ellipse(x + T*0.28, y + T*0.4, T*0.25, T*0.2, 0, 0, Math.PI*2);
  ctx.fill();
  ctx.fillRect(x + T*0.04, y + T*0.2, T*0.48, T*0.22);
  ctx.fillStyle = '#3070b0';
  ctx.beginPath();
  ctx.ellipse(x + T*0.28, y + T*0.2, T*0.24, T*0.1, 0, 0, Math.PI*2);
  ctx.fill();
  // 按钮
  ctx.fillStyle = '#e04b4b';
  ctx.fillRect(x + T*0.1, y + T*0.9, T*0.1, T*0.1);
  ctx.fillStyle = '#4ba4e0';
  ctx.fillRect(x + T*0.28, y + T*0.9, T*0.1, T*0.1);
}

function drawWhiteboard(x, y) {
  const ctx = window.CTX;
  // 框架
  ctx.fillStyle = '#8a6040';
  ctx.fillRect(x, y, T*2, T*1.3);
  // 白板面
  ctx.fillStyle = '#f8f4ec';
  ctx.fillRect(x + 5, y + 5, T*2 - 10, T*1.3 - 10);
  // 上面的内容（流程图线条）
  ctx.strokeStyle = '#4b84e0';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(x + 20, y + 20); ctx.lineTo(x + 60, y + 20);
  ctx.moveTo(x + 60, y + 20); ctx.lineTo(x + 60, y + 40);
  ctx.moveTo(x + 60, y + 40); ctx.lineTo(x + 100, y + 40);
  ctx.stroke();
  // 小方块
  ctx.fillStyle = '#e04b4b';
  ctx.fillRect(x + 20, y + 15, 15, 12);
  ctx.fillStyle = '#4be05a';
  ctx.fillRect(x + 90, y + 35, 15, 12);
}

// ── 所有家具的布局坐标 ────────────────────────────────
const FURNITURE_LAYOUT = () => {
  const R = window.ROOMS;

  return [
    // 电脑房: 4 套桌椅
    { type: 'desk',  x: R.computer.x + T*0.3,  y: R.computer.y + T*0.5  },
    { type: 'desk',  x: R.computer.x + T*2.1,  y: R.computer.y + T*0.5  },
    { type: 'desk',  x: R.computer.x + T*0.3,  y: R.computer.y + T*2.2  },
    { type: 'desk',  x: R.computer.x + T*2.1,  y: R.computer.y + T*2.2  },
    { type: 'chair', x: R.computer.x + T*0.5,  y: R.computer.y + T*1.4  },
    { type: 'chair', x: R.computer.x + T*2.3,  y: R.computer.y + T*1.4  },
    { type: 'chair', x: R.computer.x + T*0.5,  y: R.computer.y + T*3.1  },
    { type: 'chair', x: R.computer.x + T*2.3,  y: R.computer.y + T*3.1  },
    { type: 'plant', x: R.computer.x + T*0.1,  y: R.computer.y + T*3.8  },
    { type: 'plant', x: R.computer.x + T*4.5,  y: R.computer.y + T*0.1  },

    // 图书室: 3 排书架
    { type: 'shelf', x: R.library.x + T*0.2,   y: R.library.y + T*0.2   },
    { type: 'shelf', x: R.library.x + T*1.8,   y: R.library.y + T*0.2   },
    { type: 'shelf', x: R.library.x + T*3.4,   y: R.library.y + T*0.2   },
    // 图书室阅读椅
    { type: 'chair', x: R.library.x + T*0.5,   y: R.library.y + T*3.5   },
    { type: 'chair', x: R.library.x + T*2.0,   y: R.library.y + T*3.5   },

    // XL 办公室: 大桌 + 书架
    { type: 'desk',  x: R.xl.x + T*1.0,        y: R.xl.y + T*1.0        },
    { type: 'chair', x: R.xl.x + T*1.2,        y: R.xl.y + T*2.0        },
    { type: 'shelf', x: R.xl.x + T*3.2,        y: R.xl.y + T*0.2        },
    { type: 'plant', x: R.xl.x + T*0.1,        y: R.xl.y + T*0.2        },

    // 走廊
    { type: 'cooler', x: R.hallway.x + T*1.5,  y: R.hallway.y + T*0.1   },
    { type: 'plant',  x: R.hallway.x + T*5.5,  y: R.hallway.y + T*0.0   },

    // 会议室
    { type: 'table',  x: R.meeting.x + T*3.5,  y: R.meeting.y + T*0.8   },
    { type: 'board',  x: R.meeting.x + T*0.5,  y: R.meeting.y + T*0.4   },
    { type: 'plant',  x: R.meeting.x + T*0.1,  y: R.meeting.y + T*3.5   },
    { type: 'plant',  x: R.meeting.x + T*11.3, y: R.meeting.y + T*3.5   },
  ];
};

// ── 渲染所有家具 ──────────────────────────────────────
window.SpriteEngine = {
  render(ctx) {
    FURNITURE_LAYOUT().forEach(f => {
      switch(f.type) {
        case 'desk':   drawDesk(f.x, f.y);        break;
        case 'chair':  drawChair(f.x, f.y);        break;
        case 'shelf':  drawBookshelf(f.x, f.y);    break;
        case 'plant':  drawPlant(f.x, f.y);        break;
        case 'cooler': drawWaterCooler(f.x, f.y);  break;
        case 'table':  drawConferenceTable(f.x, f.y); break;
        case 'board':  drawWhiteboard(f.x, f.y);   break;
      }
    });
    // 渲染角色 (由 AgentEngine 负责)
  }
};
