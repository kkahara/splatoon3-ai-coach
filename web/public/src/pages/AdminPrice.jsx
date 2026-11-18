import { useEffect, useState } from "react";
import {
  loadAdminPrice,
  loadAdminWorkerCapacity,
  saveAdminPrice,
  saveAdminUploadLimits,
  saveAdminWorkerCapacity,
} from "../api.js";
import { limitChanges, limitInputs, limitSourceLabel, mergeLimits, UPLOAD_LIMITS } from "./adminLimits.js";
import { centsFromDollars, formatUsd } from "./submitForm.js";
import { AdminError, AdminLoading } from "./AdminUi.jsx";

const EVENT_LABELS = {
  page_viewed: "Submit page viewed",
  upload_started: "Upload started",
  upload_completed: "Upload completed",
  checkout_opened: "Checkout opened",
  payment_completed: "Payment completed",
  analysis_completed: "Analysis completed",
  analysis_failed: "Analysis failed",
};

export function AdminPricePage() {
  const [price, setPrice] = useState(null);
  const [dollars, setDollars] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  useEffect(() => {
    let stopped = false;
    loadAdminPrice()
      .then((next) => {
        if (stopped) {
          return;
        }
        setPrice(next);
        setDollars(next.price_cents == null ? "" : (next.price_cents / 100).toFixed(2));
      })
      .catch((exc) => {
        if (!stopped) {
          setError(exc.message);
        }
      });
    return () => {
      stopped = true;
    };
  }, []);

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    setSaved("");
    const cents = centsFromDollars(dollars);
    if (cents == null) {
      setError("Enter a dollar amount with at most two decimal places.");
      return;
    }
    try {
      const next = await saveAdminPrice(cents);
      setPrice(next);
      setDollars((next.price_cents / 100).toFixed(2));
      setSaved("Saved.");
    } catch (exc) {
      setError(exc.message);
    }
  }

  return (
    <section>
      <h1>Price</h1>
      <AdminError message={error} />
      {!price && !error ? <AdminLoading /> : null}
      {price ? (
        <>
          <form onSubmit={onSubmit}>
            <label>
              Price per analysis (USD)
              <input
                value={dollars}
                inputMode="decimal"
                onChange={(event) => setDollars(event.target.value)}
              />
            </label>
            <button type="submit">Save price</button>
          </form>
          {saved ? <p className="note">{saved}</p> : null}
          <UploadLimitsForm price={price} onSaved={(next) => setPrice((current) => mergeLimits(current, next))} />
          <p className="note">
            Current version {price.pricing_version ?? "—"}
            {price.price_cents == null ? "" : `, ${formatUsd(price.price_cents)}`}
          </p>
          <h2>Events</h2>
          <p className="note">
            Counts only. Page views include refreshes and repeat visits, so they are not a conversion rate.
            Compare upload started, checkout opened, and payment completed.
          </p>
          <EventTable events={price.events || []} />
        </>
      ) : null}
      <WorkerCapacitySection />
    </section>
  );
}

function UploadLimitsForm({ price, onSaved }) {
  const [inputs, setInputs] = useState(() => limitInputs(price));
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [saving, setSaving] = useState(false);

  async function send(changes) {
    setError("");
    setSaved("");
    setSaving(true);
    try {
      const next = await saveAdminUploadLimits(changes);
      onSaved(next);
      setInputs(limitInputs(next));
      setSaved("Saved.");
    } catch (exc) {
      setError(exc.message);
    } finally {
      setSaving(false);
    }
  }

  function onSubmit(event) {
    event.preventDefault();
    const result = limitChanges(price, inputs);
    if (result.error) {
      setError(result.error);
      setSaved("");
      return;
    }
    send(result.changes);
  }

  return (
    <form onSubmit={onSubmit}>
      {UPLOAD_LIMITS.map(({ key, label, resettable }) => (
        <div key={key}>
          <label>
            {label} ({limitSourceLabel(price, key)})
            <input
              value={inputs[key]}
              type="number"
              min={1}
              max={100}
              step={1}
              inputMode="numeric"
              onChange={(event) => setInputs((current) => ({ ...current, [key]: event.target.value }))}
            />
          </label>
          {resettable ? (
            <button
              type="button"
              disabled={saving || price[`${key}_source`] !== "saved"}
              onClick={() => send({ [key]: null })}
            >
              Use environment default
            </button>
          ) : null}
        </div>
      ))}
      <p className="note">
        Accepting the submission uses one hourly slot, including when the proxy then rejects the video.
        The network limit counts every submission from one network, guests and logged-in users together,
        so it applies first whenever it is lower than the guest or logged-in limit.
      </p>
      <AdminError message={error} />
      <button type="submit" disabled={saving}>
        {saving ? "Saving…" : "Save upload limits"}
      </button>
      {saved ? <p className="note">{saved}</p> : null}
    </form>
  );
}

function WorkerCapacitySection() {
  const [current, setCurrent] = useState(null);
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let stopped = false;
    loadAdminWorkerCapacity()
      .then((next) => {
        if (!stopped) {
          setCurrent(next);
          setValue(String(next.max_concurrent_jobs));
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
  }, []);

  async function onSave(event) {
    event.preventDefault();
    setError("");
    setSaved("");
    const jobs = Number(value);
    if (!Number.isInteger(jobs) || jobs < current.min || jobs > current.max) {
      setError(`Enter a whole number from ${current.min} to ${current.max}.`);
      return;
    }
    setSaving(true);
    try {
      const next = await saveAdminWorkerCapacity(jobs);
      setCurrent(next);
      setValue(String(next.max_concurrent_jobs));
      setSaved("Saved.");
    } catch (exc) {
      setError(exc.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <h2>Worker capacity</h2>
      {!current && !error ? <AdminLoading /> : null}
      {current ? (
        <form onSubmit={onSave}>
          <label>
            Videos this worker analyzes at once
            <input
              value={value}
              type="number"
              min={current.min}
              max={current.max}
              step={1}
              inputMode="numeric"
              onChange={(event) => setValue(event.target.value)}
            />
          </label>
          <p className="note">
            Worker {current.worker_key}, {current.active_jobs} running now. This is the capacity of this
            analysis worker, not a limit for the whole site; another worker would have its own value.
            Lowering it lets running analyses finish and stops new ones until fewer than the new value are running.
          </p>
          <AdminError message={error} />
          <button type="submit" disabled={saving}>
            {saving ? "Saving…" : "Save worker capacity"}
          </button>
        </form>
      ) : (
        <AdminError message={error} />
      )}
      {saved ? <p className="note">{saved}</p> : null}
    </>
  );
}

function EventTable({ events }) {
  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead>
          <tr>
            <th>Version</th>
            <th>Price</th>
            <th>Event</th>
            <th>Count</th>
          </tr>
        </thead>
        <tbody>
          {events.length === 0 ? (
            <tr>
              <td colSpan={4}>No events yet.</td>
            </tr>
          ) : null}
          {events.map((row) => (
            <tr key={`${row.pricing_version}-${row.price_cents}-${row.kind}`}>
              <td>{row.pricing_version ?? "—"}</td>
              <td>{row.price_cents == null ? "—" : formatUsd(row.price_cents)}</td>
              <td>{EVENT_LABELS[row.kind] || row.kind}</td>
              <td>{row.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
