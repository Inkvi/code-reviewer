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
    <nav className="flex items-center gap-1 text-sm text-gray-500">
      {crumbs.map((c, i) => (
        <span key={c.path} className="flex items-center gap-1">
          {i > 0 && <span>/</span>}
          {i === crumbs.length - 1 ? (
            <span className="text-gray-900 font-medium">{c.label}</span>
          ) : (
            <Link to={c.path} className="hover:text-blue-600">
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
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <Link to="/" className="text-lg font-semibold text-gray-900">
            PR Review History
          </Link>
          <Breadcrumbs />
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-6 py-8">
        <Outlet />
      </main>
    </div>
  );
}
