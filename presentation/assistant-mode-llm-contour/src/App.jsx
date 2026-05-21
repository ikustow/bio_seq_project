import React, { useEffect, useMemo, useState } from "react";

const TOTAL_MS = 60_000;

const scenes = [
  {
    eyebrow: "Scene 1 / 4",
    title: "User asks",
    copy:
      "The user asks a question in chat. Think Mode keeps the product moving by suggesting what to ask next.",
    accent: "teal",
  },
  {
    eyebrow: "Scene 2 / 4",
    title: "Context is prepared",
    copy:
      "The feature reuses the current answer, recent chat history, and the selected protein card instead of starting a new search.",
    accent: "violet",
  },
  {
    eyebrow: "Scene 3 / 4",
    title: "Three prompts are planned",
    copy:
      "The AI Agent calls context tools and turns the available context into three concise follow-up prompts.",
    accent: "amber",
  },
  {
    eyebrow: "Scene 4 / 4",
    title: "Chips appear in chat",
    copy:
      "The output is normalized, deduplicated, and rendered as three clickable chips under the assistant answer.",
    accent: "rose",
  },
];

const pipelineSteps = [
  {
    label: "User prompt",
    detail: "A new chat turn arrives from the product UI.",
    packet: "new turn",
    activeNodes: ["user", "answer"],
    edges: ["user-context"],
    position: { x: "11%", y: "15%" },
  },
  {
    label: "Context pack",
    detail: "We collect the inputs that can safely ground the suggestions.",
    packet: "context",
    activeNodes: ["context", "router"],
    edges: ["user-context", "context-llm", "llm-router"],
    position: { x: "39%", y: "20%" },
    sources: ["user question", "assistant answer", "chat history", "protein card", "open topics"],
  },
  {
    label: "Tools + 3 prompts",
    detail: "The AI Agent calls context tools and drafts three useful next prompts.",
    packet: "tools",
    activeNodes: ["llm", "tools", "retriever", "uniprot", "state"],
    edges: ["context-llm", "router-tools", "tools-faiss", "tools-uniprot", "tools-state"],
    position: { x: "50%", y: "39%" },
    tools: [
      { name: "get_current_protein_context", purpose: "facts from the protein card" },
      { name: "get_recent_dialogue_summary", purpose: "what the user and assistant discussed" },
      { name: "get_open_bioseq_threads", purpose: "topics worth asking about next" },
    ],
  },
  {
    label: "Chat chips",
    detail: "Validation keeps exactly three prompts and the UI renders them below the answer.",
    packet: "render",
    activeNodes: ["answer", "retriever", "uniprot", "state"],
    edges: ["q1-chat", "q2-chat", "q3-chat"],
    position: { x: "12%", y: "74%" },
  },
];

function App() {
  const [elapsed, setElapsed] = useState(0);
  const [isPlaying, setIsPlaying] = useState(true);
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

  return (
    <div className={`deck accent-${scene.accent}`}>
      <header className="topbar">
        <div>
          <span className="kicker">BioSeq Assistant Think Mode</span>
        </div>
      </header>

      <main className="stage">
        <section className="narrative" aria-live="polite">
          <StepCard
            activeStep={activeStep}
            activeStepIndex={activeStepIndex}
            progress={stepProgress}
            onStepClick={jumpToStep}
          />
        </section>

        <section className="loop-panel" aria-label="AI Agent suggestion flow">
          <FlowLines activeEdges={activeEdgeSet} seenEdges={seenEdgeSet} />
          <div className="panel-step-banner">
            <span>{String(activeStepIndex + 1).padStart(2, "0")} / 04</span>
            <strong>{activeStep.label}</strong>
          </div>
          <Node
            id="user"
            label="Prompt"
            meta="chat input"
            active={activeNodeSet.has("user")}
            seen={seenNodeSet.has("user")}
            badge={activeNodeSet.has("user") ? activeStepIndex + 1 : null}
          />
          <Node
            id="context"
            label="Context"
            meta="grounded inputs"
            active={activeNodeSet.has("context")}
            seen={seenNodeSet.has("context")}
            badge={activeNodeSet.has("context") ? activeStepIndex + 1 : null}
          />
          <Node
            id="llm"
            label="AI Agent"
            meta="plans 3 prompts"
            active={activeNodeSet.has("llm")}
            seen={seenNodeSet.has("llm")}
            badge={activeNodeSet.has("llm") ? activeStepIndex + 1 : null}
            core
          />
          <Node
            id="router"
            label="Inputs"
            meta="answer / history / card"
            active={activeNodeSet.has("router")}
            seen={seenNodeSet.has("router")}
            badge={activeNodeSet.has("router") ? activeStepIndex + 1 : null}
          />
          <Node
            id="tools"
            label="Tools"
            meta="context tools"
            active={activeNodeSet.has("tools")}
            seen={seenNodeSet.has("tools")}
            badge={activeNodeSet.has("tools") ? activeStepIndex + 1 : null}
          />
          <Node
            id="retriever"
            label="Protein card"
            meta="current facts"
            active={activeNodeSet.has("retriever")}
            seen={seenNodeSet.has("retriever")}
            badge={activeNodeSet.has("retriever") ? activeStepIndex + 1 : null}
          />
          <Node
            id="uniprot"
            label="Dialogue"
            meta="recent messages"
            active={activeNodeSet.has("uniprot")}
            seen={seenNodeSet.has("uniprot")}
            badge={activeNodeSet.has("uniprot") ? activeStepIndex + 1 : null}
          />
          <Node
            id="state"
            label="Topics"
            meta="open threads"
            active={activeNodeSet.has("state")}
            seen={seenNodeSet.has("state")}
            badge={activeNodeSet.has("state") ? activeStepIndex + 1 : null}
          />
          <Node
            id="answer"
            label="Chat"
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

    </div>
  );
}

function StepCard({ activeStep, activeStepIndex, progress, onStepClick }) {
  return (
    <div className="step-card">
      <div className="step-card-head">
        <span>Step {String(activeStepIndex + 1).padStart(2, "0")}</span>
        <strong>{activeStep.label}</strong>
      </div>
      <p>{activeStep.detail}</p>
      {activeStep.sources && (
        <div className="source-pills" aria-label="Context sources">
          {activeStep.sources.map((source) => (
            <span key={source}>{source}</span>
          ))}
        </div>
      )}
      {activeStep.tools && (
        <div className="tool-list" aria-label="Context tools">
          {activeStep.tools.map((tool) => (
            <span key={tool.name}>
              <strong>{tool.name}</strong>
              {tool.purpose}
            </span>
          ))}
        </div>
      )}
      <div className="step-card-progress">
        <i style={{ width: `${progress}%` }} />
      </div>
      <div className="step-rail" aria-label="Step navigation">
        {pipelineSteps.map((step, index) => (
          <button
            key={step.label}
            className={`${index === activeStepIndex ? "active" : ""} ${
              index < activeStepIndex ? "done" : ""
            }`}
            onClick={() => onStepClick(index)}
            title={step.label}
            aria-label={`Step ${index + 1}: ${step.label}`}
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
  { id: "context-llm", d: "M392 196 C444 212 470 242 452 286" },
  { id: "llm-router", d: "M572 302 C666 226 772 178 858 172" },
  { id: "router-tools", d: "M900 188 C916 222 916 260 900 292" },
  { id: "tools-faiss", d: "M852 366 C802 424 724 500 616 528" },
  { id: "tools-uniprot", d: "M896 366 C920 424 920 488 884 528" },
  { id: "tools-state", d: "M824 366 C720 432 560 508 354 528" },
  { id: "q1-chat", d: "M650 615 C530 660 330 652 190 588" },
  { id: "q2-chat", d: "M900 615 C670 684 350 672 190 588" },
  { id: "q3-chat", d: "M352 568 C304 582 248 584 190 572" },
];

function FlowLines({ activeEdges, seenEdges }) {
  return (
    <svg className="flow-lines" viewBox="0 0 1000 680" preserveAspectRatio="none">
      {flowEdges.map((edge) => {
        const isActive = activeEdges.has(edge.id);
        const isSeen = seenEdges.has(edge.id);
        return (
          <path
            key={`${edge.id}-${isActive ? "active" : "idle"}`}
            className={`line route ${isSeen ? "seen" : ""} ${isActive ? "active" : ""}`}
            d={edge.d}
          />
        );
      })}
    </svg>
  );
}

export default App;
