import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { loadAdminUser } from "../api.js";
import {
  AdminError,
  AdminLoading,
  DetailList,
  displayLabel,
  formatDate,
  formatValue,
} from "./AdminUi.jsx";

const PRIMARY_FIELDS = new Set([
  "id", "user_id", "email", "name", "display_name", "role",
  "verified", "email_verified", "created_at", "updated_at", "last_login_at",
]);

export function AdminUserDetailPage() {
  const { id } = useParams();
  const [user, setUser] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stopped = false;
    loadAdminUser(id)
      .then((next) => {
        if (!stopped) {
          setUser(next);
        }
      })
      .catch((exc) => {
        if (!stopped) {
          setError(exc.message);
        }
      });
    return () => {
      stopped = true;
    };
  }, [id]);

  return (
    <section>
      <p><Link to="/admin/users">← Users</Link></p>
      <h1>User detail</h1>
      <AdminError message={error} />
      {!user && !error ? <AdminLoading /> : null}
      {user ? <UserDetail user={user} /> : null}
    </section>
  );
}

function UserDetail({ user }) {
  const extras = Object.entries(user).filter(([key]) => !PRIMARY_FIELDS.has(key));
  return (
    <>
      <DetailList rows={[
        ["User ID", user.id ?? user.user_id],
        ["Email", user.email],
        ["Name", user.name ?? user.display_name],
        ["Role", user.role],
        ["Email verified", user.email_verified ?? user.verified],
        ["Created", formatDate(user.created_at)],
        ["Updated", formatDate(user.updated_at)],
        ["Last login", formatDate(user.last_login_at)],
      ]} />
      {extras.length ? (
        <details className="admin-raw">
          <summary>Additional user data</summary>
          <dl className="admin-details">
            {extras.map(([key, value]) => (
              <div key={key}><dt>{displayLabel(key)}</dt><dd>{formatValue(value)}</dd></div>
            ))}
          </dl>
        </details>
      ) : null}
    </>
  );
}
