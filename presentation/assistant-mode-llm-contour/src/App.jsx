import React, { useEffect, useMemo, useState } from "react";
import { ArrowLeft, ArrowRight, FileText, Pause, Play, RotateCcw } from "lucide-react";

const TOTAL_MS = 60_000;

const scenes = [
  {
    eyebrow: "Сцена 1 / 4",
    title: "Поступает вопрос",
    copy:
      "Пользователь задает вопрос в чате. Think Mode нужен, чтобы сразу предложить понятное продолжение.",
    accent: "teal",
  },
  {
    eyebrow: "Сцена 2 / 4",
    title: "Собираем контекст",
    copy:
      "Контур берет только нужные источники: вопрос, ответ, историю и текущую карточку белка.",
    accent: "violet",
  },
  {
    eyebrow: "Сцена 3 / 4",
    title: "Готовим 3 вопроса",
    copy:
      "LLM не отвечает заново, а планирует три коротких follow-up вопроса для следующего хода.",
    accent: "amber",
  },
  {
    eyebrow: "Сцена 4 / 4",
    title: "Отдаем в чат",
    copy:
      "Проверяем формат, убираем повторы и показываем три clickable chips под ответом ассистента.",
    accent: "rose",
  },
];

const pipelineSteps = [
  {
    label: "Вопрос пользователя",
    detail: "Новый turn приходит из chat UI.",
    packet: "user asks",
    activeNodes: ["user", "answer"],
    edges: ["user-context"],
    position: { x: "11%", y: "18%" },
  },
  {
    label: "Контекст",
    detail: "Собираем источники, на которые можно опереться.",
    packet: "context",
    activeNodes: ["context", "router"],
    edges: ["user-context", "context-llm", "llm-router"],
    position: { x: "39%", y: "23%" },
    sources: ["user question", "assistant answer", "chat history", "protein card", "open topics"],
  },
  {
    label: "Три идеи",
    detail: "LLM готовит короткие разные follow-up вопросы.",
    packet: "3 questions",
    activeNodes: ["llm", "tools", "retriever", "uniprot", "state"],
    edges: ["context-llm", "router-tools", "tools-faiss", "tools-uniprot", "tools-state"],
    position: { x: "50%", y: "43%" },
  },
  {
    label: "Chips в чате",
    detail: "Валидация оставляет ровно три вопроса и UI рисует их под ответом.",
    packet: "render",
    activeNodes: ["answer", "retriever", "uniprot", "state"],
    edges: ["q1-chat", "q2-chat", "q3-chat"],
    position: { x: "12%", y: "66%" },
  },
];

const speakerText = `Я бы объяснял Think Mode как маленькую фичу поверх обычного чата. Пользователь задает вопрос, ассистент отвечает, а затем отдельный контур подбирает три вопроса, которые помогут продолжить разговор.

Сначала собирается контекст из конкретных источников: последний вопрос пользователя, последний ответ ассистента, история диалога, выбранная protein card и открытые биологические темы. Важно, что здесь нет нового поиска по базе — мы используем уже доступный контекст.

После этого LLM получает узкую задачу: подготовить ровно три коротких follow-up вопроса. Мы проверяем формат, убираем повторы и отдаем эти вопросы обратно в чат как clickable chips под ответом ассистента.`;

function formatTime(ms) {
  const seconds = Math.ceil(Math.max(0, ms) / 1000);
  return `0:${String(seconds).padStart(2, "0")}`;
}

function App() {
  const [elapsed, setElapsed] = useState(0);
  const [isPlaying, setIsPlaying] = useState(true);
  const [showNotes, setShowNotes] = useState(false);
  const sceneMs = TOTAL_MS / scenes.length;
  const stepMs = TOTAL_MS / pipelineSteps.length;

  useEffect(() => {
    if (!isPlaying) return undefined;

    let frameId;
    let previous = performance.now();

    const tick = (now) => {
      const delta = now - previous;
      previous = now;
      setElapsed((current) => {
        const next = current + delta;
        if (next >= TOTAL_MS) {
          setIsPlaying(false);
          return TOTAL_MS;
        }
        return next;
      });
      frameId = requestAnimationFrame(tick);
    };

    frameId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frameId);
  }, [isPlaying]);

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.code === "Space") {
        event.preventDefault();
        setIsPlaying((current) => !current);
      }
      if (event.key === "ArrowRight") {
        setElapsed((current) => Math.min(TOTAL_MS, current + sceneMs));
      }
      if (event.key === "ArrowLeft") {
        setElapsed((current) => Math.max(0, current - sceneMs));
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [sceneMs]);

  const activeIndex = Math.min(scenes.length - 1, Math.floor(elapsed / sceneMs));
  const activeStepIndex = Math.min(pipelineSteps.length - 1, Math.floor(elapsed / stepMs));
  const scene = scenes[activeIndex];
  const activeStep = pipelineSteps[activeStepIndex];
  const remaining = TOTAL_MS - elapsed;
  const sceneProgress = ((elapsed - activeIndex * sceneMs) / sceneMs) * 100;
  const stepProgress = ((elapsed - activeStepIndex * stepMs) / stepMs) * 100;

  const activeNodeSet = useMemo(() => new Set(activeStep.activeNodes), [activeStep]);
  const seenNodeSet = useMemo(() => {
    return new Set(pipelineSteps.slice(0, activeStepIndex + 1).flatMap((step) => step.activeNodes));
  }, [activeStepIndex]);
  const activeEdgeSet = useMemo(() => new Set(activeStep.edges), [activeStep]);
  const seenEdgeSet = useMemo(() => {
    return new Set(pipelineSteps.slice(0, activeStepIndex + 1).flatMap((step) => step.edges));
  }, [activeStepIndex]);

  const jumpToStep = (index) => {
    setElapsed(index * stepMs + 40);
    setIsPlaying(true);
  };

  const reset = () => {
    setElapsed(0);
    setIsPlaying(true);
  };

  return (
    <div className={`deck accent-${scene.accent}`}>
      <header className="topbar">
        <div>
          <span className="kicker">BioSeq Assistant Think Mode</span>
          <strong>Think Mode: 3 follow-up вопроса</strong>
        </div>

        <div className="controls">
          <button
            aria-label="Назад"
            title="Назад"
            onClick={() => setElapsed((current) => Math.max(0, current - sceneMs))}
          >
            <ArrowLeft size={18} />
          </button>
          <button
            aria-label={isPlaying ? "Пауза" : "Пуск"}
            title={isPlaying ? "Пауза" : "Пуск"}
            onClick={() => setIsPlaying((current) => !current)}
          >
            {isPlaying ? <Pause size={18} /> : <Play size={18} />}
          </button>
          <button aria-label="Сначала" title="Сначала" onClick={reset}>
            <RotateCcw size={18} />
          </button>
          <button
            aria-label="Вперед"
            title="Вперед"
            onClick={() => setElapsed((current) => Math.min(TOTAL_MS, current + sceneMs))}
          >
            <ArrowRight size={18} />
          </button>
          <button
            aria-label="Текст речи"
            title="Текст речи"
            className={showNotes ? "selected" : ""}
            onClick={() => setShowNotes((current) => !current)}
          >
            <FileText size={18} />
          </button>
        </div>
      </header>

      <main className="stage">
        <section className="narrative" aria-live="polite">
          <p className="scene-label">{scene.eyebrow}</p>
          <h1>{scene.title}</h1>
          <p className="scene-copy">{scene.copy}</p>

          <StepCard
            activeStep={activeStep}
            activeStepIndex={activeStepIndex}
            progress={stepProgress}
            onStepClick={jumpToStep}
          />

          <div className="timer">
            <span>{formatTime(remaining)}</span>
            <div className="timer-track">
              <div className="timer-fill" style={{ width: `${sceneProgress}%` }} />
            </div>
          </div>
        </section>

        <section className="loop-panel" aria-label="Схема LLM-контура">
          <FlowLines activeEdges={activeEdgeSet} seenEdges={seenEdgeSet} />
          <div className="panel-step-banner">
            <span>{String(activeStepIndex + 1).padStart(2, "0")} / 04</span>
            <strong>{activeStep.label}</strong>
          </div>
          <Node
            id="user"
            label="Вопрос"
            meta="user prompt"
            active={activeNodeSet.has("user")}
            seen={seenNodeSet.has("user")}
            badge={activeNodeSet.has("user") ? activeStepIndex + 1 : null}
          />
          <Node
            id="context"
            label="Контекст"
            meta="sources"
            active={activeNodeSet.has("context")}
            seen={seenNodeSet.has("context")}
            badge={activeNodeSet.has("context") ? activeStepIndex + 1 : null}
          />
          <Node
            id="llm"
            label="LLM"
            meta="plans 3 questions"
            active={activeNodeSet.has("llm")}
            seen={seenNodeSet.has("llm")}
            badge={activeNodeSet.has("llm") ? activeStepIndex + 1 : null}
            core
          />
          <Node
            id="router"
            label="Sources"
            meta="answer / history / card"
            active={activeNodeSet.has("router")}
            seen={seenNodeSet.has("router")}
            badge={activeNodeSet.has("router") ? activeStepIndex + 1 : null}
          />
          <Node
            id="tools"
            label="Validate"
            meta="exactly 3"
            active={activeNodeSet.has("tools")}
            seen={seenNodeSet.has("tools")}
            badge={activeNodeSet.has("tools") ? activeStepIndex + 1 : null}
          />
          <Node
            id="retriever"
            label="Q1"
            meta="function"
            active={activeNodeSet.has("retriever")}
            seen={seenNodeSet.has("retriever")}
            badge={activeNodeSet.has("retriever") ? activeStepIndex + 1 : null}
          />
          <Node
            id="uniprot"
            label="Q2"
            meta="evidence"
            active={activeNodeSet.has("uniprot")}
            seen={seenNodeSet.has("uniprot")}
            badge={activeNodeSet.has("uniprot") ? activeStepIndex + 1 : null}
          />
          <Node
            id="state"
            label="Q3"
            meta="limits"
            active={activeNodeSet.has("state")}
            seen={seenNodeSet.has("state")}
            badge={activeNodeSet.has("state") ? activeStepIndex + 1 : null}
          />
          <Node
            id="answer"
            label="Чат"
            meta="3 chips"
            active={activeNodeSet.has("answer")}
            seen={seenNodeSet.has("answer")}
            badge={activeNodeSet.has("answer") ? activeStepIndex + 1 : null}
          />

          <div
            className="step-packet"
            style={{ left: activeStep.position.x, top: activeStep.position.y }}
          >
            <span>{activeStep.packet}</span>
          </div>
        </section>
      </main>

      {showNotes && (
        <aside className="notes" aria-label="Текст речи">
          <h2>Речь на 60 секунд</h2>
          {speakerText.split("\n\n").map((paragraph) => (
            <p key={paragraph}>{paragraph}</p>
          ))}
        </aside>
      )}
    </div>
  );
}

function StepCard({ activeStep, activeStepIndex, progress, onStepClick }) {
  return (
    <div className="step-card">
      <div className="step-card-head">
        <span>Шаг {String(activeStepIndex + 1).padStart(2, "0")}</span>
        <strong>{activeStep.label}</strong>
      </div>
      <p>{activeStep.detail}</p>
      {activeStep.sources && (
        <div className="source-pills" aria-label="Источники контекста">
          {activeStep.sources.map((source) => (
            <span key={source}>{source}</span>
          ))}
        </div>
      )}
      <div className="step-card-progress">
        <i style={{ width: `${progress}%` }} />
      </div>
      <div className="step-rail" aria-label="Переход по шагам">
        {pipelineSteps.map((step, index) => (
          <button
            key={step.label}
            className={`${index === activeStepIndex ? "active" : ""} ${
              index < activeStepIndex ? "done" : ""
            }`}
            onClick={() => onStepClick(index)}
            title={step.label}
            aria-label={`Шаг ${index + 1}: ${step.label}`}
          >
            {index + 1}
          </button>
        ))}
      </div>
    </div>
  );
}

function Node({ id, label, meta, active, seen, badge, core = false }) {
  return (
    <div
      className={`node node-${id} ${seen ? "seen" : ""} ${active ? "active" : ""} ${
        core ? "core" : ""
      }`}
    >
      {badge && <i className="node-badge">{String(badge).padStart(2, "0")}</i>}
      <strong>{label}</strong>
      <span>{meta}</span>
    </div>
  );
}

const flowEdges = [
  { id: "user-context", d: "M190 134 C238 112 300 118 356 150" },
  { id: "context-llm", d: "M420 226 C456 252 490 288 514 330" },
  { id: "llm-router", d: "M600 260 C692 190 778 166 858 172" },
  { id: "router-tools", d: "M905 248 C892 315 878 356 850 418" },
  { id: "tools-faiss", d: "M818 440 C764 484 714 496 655 500" },
  { id: "tools-uniprot", d: "M872 438 C890 470 908 490 925 504" },
  { id: "tools-state", d: "M800 430 C684 482 560 497 442 500" },
  { id: "q1-chat", d: "M650 615 C530 660 330 652 190 588" },
  { id: "q2-chat", d: "M900 615 C670 684 350 672 190 588" },
  { id: "q3-chat", d: "M420 615 C340 646 250 632 190 588" },
];

function FlowLines({ activeEdges, seenEdges }) {
  return (
    <svg className="flow-lines" viewBox="0 0 1000 680" preserveAspectRatio="none">
      <defs>
        <marker
          id="arrow-active"
          markerHeight="8"
          markerWidth="8"
          orient="auto"
          refX="7"
          refY="4"
          viewBox="0 0 8 8"
        >
          <path d="M0 0 L8 4 L0 8 Z" className="arrow-head" />
        </marker>
      </defs>
      {flowEdges.map((edge) => {
        const isActive = activeEdges.has(edge.id);
        const isSeen = seenEdges.has(edge.id);
        return (
          <path
            key={`${edge.id}-${isActive ? "active" : "idle"}`}
            className={`line route ${isSeen ? "seen" : ""} ${isActive ? "active" : ""}`}
            d={edge.d}
            markerEnd={isActive ? "url(#arrow-active)" : undefined}
          />
        );
      })}
    </svg>
  );
}

export default App;
