'use strict';

// 1. 단계 선택기 — 버튼 목록과 패널이 일치할 때만 활성화한다.
const steps = Array.from(document.querySelectorAll('[data-step]'));
const panels = Array.from(document.querySelectorAll('.panel'));

function selectStep(button) {
  for (const step of steps) {
    step.setAttribute('aria-pressed', String(step === button));
  }
  for (const panel of panels) {
    panel.hidden = panel.id !== button.dataset.step;
  }
}

if (steps.length && steps.every((step) => panels.some((panel) => panel.id === step.dataset.step))) {
  document.body.classList.add('enhanced');
  for (const [index, step] of steps.entries()) {
    step.addEventListener('click', () => selectStep(step));
    step.addEventListener('keydown', (event) => {
      let target;
      if (event.key === 'ArrowDown' || event.key === 'ArrowRight') target = (index + 1) % steps.length;
      if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') target = (index + steps.length - 1) % steps.length;
      if (event.key === 'Home') target = 0;
      if (event.key === 'End') target = steps.length - 1;
      if (target === undefined) return;
      event.preventDefault();
      steps[target].focus();
      selectStep(steps[target]);
    });
  }
  selectStep(steps[0]);
}

// 2. 가중평균 데모 — 모든 수치는 가상 값이며 어떤 실제 모델과도 무관하다.
const TASKS = ['code', 'agent'];
const MODELS = [
  { selector: 'local/alpha', shares: { code: 0.32, agent: 0.18 }, order: 0 },
  { selector: 'local/beta', shares: { code: 0.21, agent: 0.27 }, order: 1 },
  { selector: 'local/gamma', shares: { code: 0, agent: 0 }, order: 2 },
];

const form = document.getElementById('demo-form');
const scoreList = document.getElementById('score-list');
const previewYaml = document.getElementById('preview-yaml');
const statusLine = document.getElementById('demo-status');

function fmt(value) {
  return value.toFixed(3);
}

function readControls() {
  const weights = {
    code: Number(form.elements['w-code'].value),
    agent: Number(form.elements['w-agent'].value),
  };
  const filters = {
    'local/beta': form.elements['tools-beta'].checked ? null : '도구 호출 미지원(가상)으로 조건 검사 탈락',
    'local/alpha': form.elements['ctx-alpha'].checked ? null : '최소 컨텍스트 미충족(가상)으로 조건 검사 탈락',
  };
  const topK = Number(form.elements['top-k'].value);
  return { weights, filters, topK };
}

function computeDemo({ weights, filters, topK }) {
  const active = TASKS.filter((task) => weights[task] > 0);
  const totalWeight = active.reduce((sum, task) => sum + weights[task], 0);
  const rows = [];
  if (totalWeight === 0) {
    return { rows, selected: [], halt: '이 역할에 매칭되는 작업 태그가 없습니다. 실제 도구는 부분 프리셋을 만들지 않고 기존 프리셋을 유지합니다.' };
  }
  const scored = [];
  for (const model of MODELS) {
    const rejected = filters[model.selector];
    if (rejected) {
      rows.push({ selector: model.selector, detail: rejected, state: 'excluded', label: '제외' });
      continue;
    }
    const terms = active.map((task) => `${model.shares[task].toFixed(2)}×${weights[task]}`).join(' + ');
    const score = active.reduce((sum, task) => sum + model.shares[task] * weights[task], 0) / totalWeight;
    if (score <= 0) {
      rows.push({ selector: model.selector, detail: '관측된 양의 점유율이 없어 후보에서 제외', state: 'excluded', label: '제외' });
      continue;
    }
    scored.push({ selector: model.selector, score, order: model.order, detail: `(${terms}) ÷ ${totalWeight} = ${fmt(score)}` });
  }
  scored.sort((a, b) => b.score - a.score || a.order - b.order);
  if (!scored.length) {
    return { rows, selected: [], halt: '조건을 통과하고 점유율이 관측된 후보가 없습니다. 실제 도구는 갱신을 중단하고 기존 프리셋을 유지합니다.' };
  }
  const selected = scored.slice(0, topK);
  const ranked = scored.map((item, index) => ({
    selector: item.selector,
    detail: item.detail,
    state: index < topK ? 'selected' : 'dropped',
    label: index === 0 ? 'primary' : index < topK ? 'fallback' : 'top_k 밖',
  }));
  return { rows: ranked.concat(rows), selected: selected.map((item) => item.selector), halt: null };
}

function renderRow(row, index) {
  const li = document.createElement('li');
  li.className = `score-item ${row.state}`;
  const rank = document.createElement('span');
  rank.className = 'rank';
  rank.textContent = row.state === 'excluded' ? '—' : String(index + 1).padStart(2, '0');
  const body = document.createElement('div');
  const name = document.createElement('strong');
  name.textContent = row.selector;
  const detail = document.createElement('small');
  detail.textContent = row.detail;
  body.append(name, detail);
  const label = document.createElement('b');
  label.textContent = row.label;
  li.append(rank, body, label);
  return li;
}

function renderDemo() {
  const result = computeDemo(readControls());
  scoreList.replaceChildren(...result.rows.map(renderRow));
  if (result.halt) {
    previewYaml.textContent = '# 변경 없음 — 기존 관리 프리셋을 유지합니다.';
    statusLine.textContent = result.halt;
    statusLine.classList.add('halt');
    return;
  }
  const list = result.selected.map((selector) => `"${selector}"`).join(', ');
  previewYaml.textContent = `profiles:\n  or-demo:\n    required_providers: []\n    display_name: or-demo\n    model_mapping:\n      default: [${list}]`;
  statusLine.textContent = '설명용 미리보기입니다. 실제 도구도 --apply 없이는 모델 설정을 바꾸지 않지만, 캐시와 보고서는 기록할 수 있습니다.';
  statusLine.classList.remove('halt');
}

if (form && scoreList && previewYaml && statusLine) {
  form.classList.add('live');
  for (const input of form.querySelectorAll('input')) input.disabled = false;
  for (const name of ['w-code', 'w-agent', 'top-k']) {
    const input = form.elements[name];
    const output = document.getElementById(`${name}-out`);
    input.addEventListener('input', () => {
      output.textContent = input.value;
      renderDemo();
    });
  }
  for (const name of ['tools-beta', 'ctx-alpha']) {
    form.elements[name].addEventListener('change', renderDemo);
  }
  form.addEventListener('submit', (event) => event.preventDefault());
  renderDemo();
}
