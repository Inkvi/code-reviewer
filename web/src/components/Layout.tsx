import { Link, Outlet, useLocation } from "react-router-dom";

function Breadcrumbs() {
  const location = useLocation();
  const parts = location.pathname.split("/").filter(Boolean);
  const crumbs: { label: string; path: string }[] = [
    { label: "Repos", path: "/" },
  ];

  if (parts.length >= 2) {
    crumbs.push({
      label: `${parts[0]}/${parts[1]}`,
      path: `/${parts[0]}/${parts[1]}`,
    });
  }
  if (parts.length >= 3 && parts[2] !== "history") {
    crumbs.push({
      label: `PR #${parts[2]}`,
      path: `/${parts[0]}/${parts[1]}/${parts[2]}`,
    });
  }
  if (parts.includes("history")) {
    const idx = parts.indexOf("history");
    crumbs.push({
      label: `PR #${parts[2]}`,
      path: `/${parts[0]}/${parts[1]}/${parts[2]}`,
    });
    if (idx === parts.length - 1) {
      crumbs.push({ label: "History", path: location.pathname });
    } else {
      crumbs.push({
        label: "History",
        path: `/${parts[0]}/${parts[1]}/${parts[2]}/history`,
      });
      crumbs.push({ label: parts[idx + 1], path: location.pathname });
    }
  }

  return (
    <nav className="flex items-center gap-1.5 text-sm">
      {crumbs.map((c, i) => (
        <span key={c.path} className="flex items-center gap-1.5">
          {i > 0 && (
            <svg
              className="w-3.5 h-3.5 text-gray-600"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M9 5l7 7-7 7"
              />
            </svg>
          )}
          {i === crumbs.length - 1 ? (
            <span className="text-gray-300 font-medium">{c.label}</span>
          ) : (
            <Link
              to={c.path}
              className="text-gray-500 hover:text-accent-blue transition-colors"
            >
              {c.label}
            </Link>
          )}
        </span>
      ))}
    </nav>
  );
}

export default function Layout() {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-50 bg-surface-1/80 backdrop-blur-xl border-b border-surface-border">
        <div className="max-w-6xl mx-auto flex items-center justify-between px-6 py-3.5">
          <Link to="/" className="flex items-center gap-2.5 group">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-accent-blue to-accent-purple flex items-center justify-center shadow-lg shadow-accent-blue/20">
              <svg
                className="w-4 h-4 text-white"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2.5}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
                />
              </svg>
            </div>
            <span className="text-[15px] font-bold text-gray-100 tracking-tight group-hover:text-white transition-colors">
              PR Reviews
            </span>
          </Link>
          <Breadcrumbs />
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-6 py-8 animate-fade-in">
        <Outlet />
      </main>
    </div>
  );
}
