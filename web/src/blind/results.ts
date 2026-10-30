export {};
interface Pair { locale: string; x: string; y: string; n: number; x_pct: number; same_pct: number;
  y_pct: number; win_rate_excluding_ties: number | null; sign_p_two_sided: number }
interface Result { n: number; pairs: Pair[]; pronunciation: { voice: string; rate: number; marked: number; heard: number }[] }
const $ = (id: string): HTMLElement => document.getElementById(id)!;
const filters = ['locale', 'native', 'device'] as const;
const percent = (n: number) => (100 * n).toFixed(1) + '%';
async function refresh() {
  const query = new URLSearchParams();
  for (const key of filters) { const value = ($(key) as HTMLSelectElement).value; if (value) query.set(key, value); }
  const url = '/api/blind/results?' + query;
  ($('json') as HTMLAnchorElement).href = url;
  ($('csv') as HTMLAnchorElement).href = url + '&format=csv';
  try {
    const response = await fetch(url); if (!response.ok) throw Error('집계를 불러올 수 없습니다.');
    const result: Result = await response.json();
    $('count').textContent = '총 ' + result.n + '개 응답'; $('pairs').replaceChildren();
    for (const pair of result.pairs) {
      const item = document.createElement('div'); item.className = 'result';
      const title = document.createElement('strong'); title.textContent = pair.locale + ' · ' + pair.x + ' / ' + pair.y; item.append(title);
      const bar = document.createElement('div'); bar.className = 'bar';
      bar.setAttribute('aria-label', `${pair.x_pct.toFixed(1)}% / ${pair.same_pct.toFixed(1)}% / ${pair.y_pct.toFixed(1)}%`);
      for (const value of [pair.x_pct, pair.same_pct, pair.y_pct]) { const segment = document.createElement('span'); segment.style.width = value + '%'; bar.append(segment); }
      item.append(bar);
      const detail = document.createElement('p');
      detail.textContent = `${pair.x_pct.toFixed(1)}% / ${pair.same_pct.toFixed(1)}% / ${pair.y_pct.toFixed(1)}% · n=${pair.n} · 동점 제외 왼쪽 승률 ${pair.win_rate_excluding_ties === null ? '—' : percent(pair.win_rate_excluding_ties)} · 양측 부호검정 p=${pair.sign_p_two_sided.toFixed(4)}`;
      item.append(detail); $('pairs').append(item);
    }
    if (!result.n) $('pairs').textContent = '아직 저장된 평가가 없습니다.';
    $('pronunciation').replaceChildren();
    for (const value of result.pronunciation) {
      const line = document.createElement('p'); line.textContent = `${value.voice}: ${percent(value.rate)} (${value.marked}/${value.heard})`;
      $('pronunciation').append(line);
    }
  } catch (error) { $('status').textContent = error instanceof Error ? error.message : '집계를 불러올 수 없습니다.'; }
}
for (const key of filters) $(key).onchange = () => { void refresh(); };
void refresh();
