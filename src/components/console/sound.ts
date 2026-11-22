// 告警声音：总控台开着时，出现新告警就响一下（2026-11-22 用户要求）。用 Web Audio 现场合成，不放音频文件。
// 浏览器要用户点过页面才允许出声，所以由「🔔」按钮打开；开关存在本机
const KEY = 'console-sound';
let ctx: AudioContext | null = null;

export function soundOn(): boolean {
  try {
    return localStorage.getItem(KEY) === '1';
  } catch {
    return false;
  }
}

export function setSound(on: boolean) {
  try {
    localStorage.setItem(KEY, on ? '1' : '0');
  } catch {
    /* 存不了就只在这次打开里有效 */
  }
  if (on) beep(false); // 点按钮时试听一下，同时解锁浏览器的声音
}

/** 异常：三声高音；注意：两声低音 */
export function beep(bad: boolean) {
  try {
    ctx ??= new AudioContext();
    if (ctx.state === 'suspended') void ctx.resume();
    const tones = bad ? [988, 988, 988] : [660, 660];
    tones.forEach((f, i) => {
      const t = ctx!.currentTime + i * 0.22;
      const o = ctx!.createOscillator();
      const g = ctx!.createGain();
      o.type = 'sine';
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.3, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
      o.connect(g).connect(ctx!.destination);
      o.start(t);
      o.stop(t + 0.2);
    });
  } catch {
    /* 浏览器不支持就不响 */
  }
}
