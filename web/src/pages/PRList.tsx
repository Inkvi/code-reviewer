import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchPRs, type PRSummary } from "../api";
import { DecisionBadge, TypeBadge, StageBadge } from "../components/Badge";

export default function PRList() {
  const { org, repo } = useParams<{ org: string; repo: string }>();
  const [prs, setPrs] = useState<PRSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!org || !repo) return;
    fetchPRs(org, repo)
      .then(setPrs)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [org, repo]);

  if (loading)
    return (
      <div className="flex items-center gap-3 text-gray-500 py-12">
        <div className="w-4 h-4 border-2 border-accent-blue/30 border-t-accent-blue rounded-full animate-spin" />
        Loading...
      </div>
    );
  if (error)
    return (
      <p className="text-rose-400 bg-rose-500/10 border border-rose-500/20 rounded-lg px-4 py-3">
        Error: {error}
      </p>
    );
  if (prs.length === 0)
    return <p className="text-gray-500">No PRs reviewed for this repo.</p>;

  return (
    <div className="animate-fade-in-up">
      <h1 className="text-2xl font-bold text-gray-100 mb-8 tracking-tight">
        <span className="text-gray-500">{org}/</span>
        {repo}
      </h1>
      <div className="bg-surface-2 rounded-xl border border-surface-border overflow-hidden">
        <table className="min-w-full">
          <thead>
            <tr className="border-b border-surface-border">
              <th className="px-5 py-3 text-left text-[11px] font-semibold text-gray-500 uppercase tracking-widest font-mono">
                PR
              </th>
              <th className="px-5 py-3 text-left text-[11px] font-semibold text-gray-500 uppercase tracking-widest font-mono">
                Decision
              </th>
              <th className="px-5 py-3 text-left text-[11px] font-semibold text-gray-500 uppercase tracking-widest font-mono">
                Type
              </th>
              <th className="px-5 py-3 text-left text-[11px] font-semibold text-gray-500 uppercase tracking-widest font-mono">
                Stages
              </th>
              <th className="px-5 py-3 text-left text-[11px] font-semibold text-gray-500 uppercase tracking-widest font-mono">
                Versions
              </th>
            </tr>
          </thead>
          <tbody>
            {prs.map((pr, i) => (
              <tr
                key={pr.number}
                className="border-b border-surface-3 last:border-0 hover:bg-surface-3/50 transition-colors"
                style={{ animationDelay: `${i * 30}ms` }}
              >
                <td className="px-5 py-3.5">
                  <Link
                    to={`/${org}/${repo}/${pr.number}`}
                    className="group"
                  >
                    <span className="text-accent-blue group-hover:text-blue-300 font-mono font-semibold transition-colors">
                      #{pr.number}
                    </span>
                    {pr.title && (
                      <span className="ml-2 text-sm text-gray-400 group-hover:text-gray-300 transition-colors">
                        {pr.title}
                      </span>
                    )}
                    {pr.author && (
                      <span className="ml-1.5 text-xs text-gray-600 font-mono">
                        {pr.author}
                      </span>
                    )}
                  </Link>
                </td>
                <td className="px-5 py-3.5">
                  <DecisionBadge decision={pr.decision} />
                </td>
                <td className="px-5 py-3.5">
                  <TypeBadge type={pr.review_type} />
                </td>
                <td className="px-5 py-3.5">
                  <div className="flex gap-1.5 flex-wrap">
                    {pr.stages
                      .filter((s) => !s.endsWith(".prompt"))
                      .map((s) => (
                        <StageBadge key={s} stage={s} />
                      ))}
                  </div>
                </td>
                <td className="px-5 py-3.5">
                  {pr.version_count > 0 && (
                    <Link
                      to={`/${org}/${repo}/${pr.number}/history`}
                      className="text-sm text-gray-400 hover:text-accent-blue font-mono transition-colors"
                    >
                      {pr.version_count} ver
                      {pr.version_count !== 1 ? "s" : ""}
                    </Link>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
