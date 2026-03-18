import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchRepos, type Repo } from "../api";

export default function RepoList() {
  const [repos, setRepos] = useState<Repo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchRepos()
      .then(setRepos)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

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
  if (repos.length === 0)
    return (
      <div className="text-center py-20">
        <div className="text-4xl mb-4 opacity-40">
          <svg
            className="w-12 h-12 mx-auto text-gray-600"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"
            />
          </svg>
        </div>
        <p className="text-gray-500">
          No reviews found. Run some PR reviews first.
        </p>
      </div>
    );

  return (
    <div className="animate-fade-in-up">
      <h1 className="text-2xl font-bold text-gray-100 mb-8 tracking-tight">
        Repositories
      </h1>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {repos.map((r, i) => (
          <Link
            key={`${r.org}/${r.repo}`}
            to={`/${r.org}/${r.repo}`}
            className="group block bg-surface-2 rounded-xl border border-surface-border p-5 hover:border-surface-border-hover hover:bg-surface-3 transition-all duration-200"
            style={{ animationDelay: `${i * 60}ms` }}
          >
            <div className="text-xs font-mono text-gray-500 tracking-wider uppercase">
              {r.org}
            </div>
            <div className="text-lg font-bold text-gray-100 mt-1 group-hover:text-accent-blue transition-colors">
              {r.repo}
            </div>
            <div className="mt-3 flex items-center gap-2">
              <span className="text-sm font-mono text-gray-500">
                {r.pr_count}
              </span>
              <span className="text-sm text-gray-600">
                PR{r.pr_count !== 1 ? "s" : ""} reviewed
              </span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
