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

  if (loading) return <p className="text-gray-500">Loading...</p>;
  if (error) return <p className="text-red-600">Error: {error}</p>;
  if (repos.length === 0)
    return (
      <p className="text-gray-500">
        No reviews found. Run some PR reviews first.
      </p>
    );

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Repositories</h1>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {repos.map((r) => (
          <Link
            key={`${r.org}/${r.repo}`}
            to={`/${r.org}/${r.repo}`}
            className="block bg-white rounded-lg border border-gray-200 p-5 hover:border-blue-400 hover:shadow-sm transition-all"
          >
            <div className="text-sm text-gray-500">{r.org}</div>
            <div className="text-lg font-semibold text-gray-900">{r.repo}</div>
            <div className="mt-2 text-sm text-gray-500">
              {r.pr_count} PR{r.pr_count !== 1 ? "s" : ""} reviewed
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
