"use client";

import { FormEvent, useEffect, useState } from "react";

type Citation = {
  citation_id: string;
  title: string;
  url: string;
  section: string;
  chunk_id: string;
  retrieved_at: string;
};

type ChatResponse = {
  answer: string;
  citations: Citation[];
  refused: boolean;
  confidence: string;
};

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function Home() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<ChatResponse | null>(null);
  const [indexReady, setIndexReady] = useState(false);
  const [isAsking, setIsAsking] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    async function checkIndex() {
      try {
        const response = await fetch(`${API}/api/status`);
        const status = await response.json();
        setIndexReady(response.ok && status.vector_chunks > 0);
      } catch {
        setIndexReady(false);
      }
    }
    void checkIndex();
    const timer = window.setInterval(() => void checkIndex(), 5000);
    return () => window.clearInterval(timer);
  }, []);

  async function ask(event: FormEvent) {
    event.preventDefault();
    if (!question.trim() || !indexReady || isAsking) return;
    setIsAsking(true);
    setResult(null);
    setError("");
    try {
      const response = await fetch(`${API}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "Unable to answer the question.");
      setResult(body as ChatResponse);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to answer the question.");
    } finally {
      setIsAsking(false);
    }
  }

  return (
    <main className="appShell">
      <header className="projectHeader">
        <h1>SIA</h1>
        <p>Sports Interactive Agent</p>
      </header>

      <div className="applicationGrid">
        <aside className="referencesSection" aria-label="Answer references">
          <div className="sectionHeading">
            <span>01</span>
            <h2>References</h2>
          </div>

          <div className="referenceList">
            {result?.citations.length ? (
              result.citations.map((citation) => (
                <a
                  key={citation.chunk_id}
                  className="referenceCard"
                  href={citation.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  <div className="referenceTopline">
                    <b>[{citation.citation_id}]</b>
                    <span>NBA.COM ↗</span>
                  </div>
                  <h3>{citation.title}</h3>
                  <p>{citation.section}</p>
                  <dl>
                    <div>
                      <dt>Chunk</dt>
                      <dd>{citation.chunk_id}</dd>
                    </div>
                    <div>
                      <dt>Captured</dt>
                      <dd>{new Date(citation.retrieved_at).toLocaleString()}</dd>
                    </div>
                  </dl>
                </a>
              ))
            ) : (
              <p className="referencePlaceholder">
                References supporting the response will appear here.
              </p>
            )}
          </div>
        </aside>

        <div className="interactionColumn">
          <section className="questionSection">
            <div className="sectionHeading">
              <span>02</span>
              <h2>Question</h2>
            </div>

            <form onSubmit={ask}>
              <textarea
                value={question}
                maxLength={800}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="Enter your NBA question..."
                aria-label="NBA question"
              />
              <button disabled={!question.trim() || !indexReady || isAsking}>
                {isAsking ? "Working…" : "Submit question"}
              </button>
            </form>
          </section>

          <section className="responseSection" aria-live="polite">
            <div className="sectionHeading">
              <span>03</span>
              <h2>Response</h2>
            </div>

            <div className={`responseBody ${result?.refused ? "isRefusal" : ""}`}>
              {isAsking ? (
                <p className="responsePlaceholder">Searching the stored evidence and preparing a response…</p>
              ) : error ? (
                <p className="errorMessage">{error}</p>
              ) : result ? (
                <p>{result.answer}</p>
              ) : (
                <p className="responsePlaceholder">
                  The grounded response will appear here after you submit a question.
                </p>
              )}
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}
