export {};
type Side = 'a' | 'b';
interface Assignment { id: string; locale: string; text: string; a: Clip; b: Clip }
interface Clip { url: string; duration_ms: number }
type Elements = {
  alias: HTMLInputElement; native: HTMLSelectElement; device: HTMLSelectElement;
  locale: HTMLSelectElement; profile: HTMLElement; evaluation: HTMLElement;
  'profile-form': HTMLFormElement; 'vote-form': HTMLFormElement;
  'vote-fields': HTMLFieldSetElement; 'play-a': HTMLButtonElement; 'play-b': HTMLButtonElement;
  'issue-a': HTMLInputElement; 'issue-b': HTMLInputElement; comment: HTMLTextAreaElement;
  status: HTMLElement; sentence: HTMLElement; 'listen-status': HTMLElement;
};
const $ = <K extends keyof Elements>(id: K): Elements[K] => document.getElementById(id) as Elements[K];
const sides: Side[] = ['a', 'b'];
let assignment: Assignment | null = null;
let audios: Partial<Record<Side, HTMLAudioElement>> = {};
let heard = { a: 0, b: 0 };
let busy = false;
let loadVersion = 0;
const message = (error: unknown) => error instanceof Error ? error.message : '요청에 실패했습니다.';
async function api<T>(path: string, body?: object): Promise<T> {
  const response = await fetch('/api/blind/' + path, {
    headers: body ? { 'Content-Type': 'application/json' } : {},
    ...(body ? { method: 'POST', body: JSON.stringify(body) } : {}),
  });
  if (!response.ok) {
    const error = await response.json();
    throw Error(typeof error.detail === 'string' ? error.detail : '요청을 확인해 주세요.');
  }
  return response.json();
}
function stop() {
  Object.values(audios).forEach(audio => { audio.onended = null; audio.onerror = null; audio.pause(); audio.removeAttribute('src'); audio.load(); });
  audios = {};
}
async function next() {
  const version = ++loadVersion;
  stop(); assignment = null; heard = { a: 0, b: 0 };
  $('vote-form').reset(); $('vote-fields').disabled = true; $('status').textContent = '';
  $('listen-status').textContent = '두 음성을 끝까지 들으면 투표할 수 있습니다.';
  for (const side of sides) {
    $('play-' + side as 'play-a' | 'play-b').textContent = '▶ ' + side.toUpperCase() + ' 듣기';
    $('play-' + side as 'play-a' | 'play-b').disabled = true;
  }
  const pair = await api<Assignment | null>('next?locale=' + encodeURIComponent($('locale').value));
  if (version !== loadVersion) return;
  assignment = pair;
  if (!pair) { $('sentence').textContent = '이 언어에서 평가할 비교가 더 없습니다.'; return; }
  $('sentence').textContent = pair.text;
  for (const side of sides) {
    const audio = new Audio(); audio.preload = 'none'; audio.src = pair[side].url;
    audios[side] = audio;
    audio.onended = () => {
      if (assignment !== pair) return;
      heard[side] = pair[side].duration_ms;
      $(`play-${side}`).textContent = '✓ ' + side.toUpperCase() + ' 다시 듣기';
      $('vote-fields').disabled = !(heard.a && heard.b);
      if (heard.a && heard.b) $('listen-status').textContent = '두 음성을 모두 들었습니다. 평가를 선택해 주세요.';
    };
    audio.onerror = () => { $('status').textContent = '오디오를 불러올 수 없습니다. 세션을 다시 시작해 주세요.'; };
    $(`play-${side}`).disabled = false;
  }
}
$('profile-form').onsubmit = async event => {
  event.preventDefault();
  try {
    await api('session', { alias: $('alias').value, native: $('native').value, device: $('device').value });
    $('profile').hidden = true; $('evaluation').hidden = false;
    if (['en', 'zh-CN', 'zh-TW', 'ja', 'yue'].includes($('native').value)) $('locale').value = $('native').value;
    await next();
  } catch (error) { $('status').textContent = message(error); }
};
$('locale').onchange = () => { void next().catch(error => { $('status').textContent = message(error); }); };
for (const side of sides) $(`play-${side}`).onclick = async () => {
  try {
    Object.values(audios).forEach(audio => audio.pause());
    const audio = audios[side]; if (!audio) return;
    audio.currentTime = 0; await audio.play();
  } catch { $('status').textContent = '재생에 실패했습니다. 다시 눌러 주세요.'; }
};
$('vote-form').onsubmit = async event => {
  event.preventDefault();
  if (busy || !assignment || !heard.a || !heard.b) return;
  const choice = new FormData($('vote-form')).get('choice');
  busy = true; $('vote-fields').disabled = true;
  try {
    await api('vote', { assignment_id: assignment.id, choice, pron_issue_a: $('issue-a').checked,
      pron_issue_b: $('issue-b').checked, listened_ms_a: heard.a, listened_ms_b: heard.b, comment: $('comment').value });
    await next();
  } catch (error) { $('status').textContent = message(error); $('vote-fields').disabled = false; }
  finally { busy = false; }
};
