import { useEffect, useRef, useState } from "react";
import type { AuthStatus, Status } from "./api";
import { deleteJson, getJson, postFile } from "./api";
import { updatedLabel } from "./format";
import { t, tx } from "./i18n";
import { Button, Dialog, Notice, toast } from "./ui";

/** Picks a backup file, asks before replacing the data, uploads it and reloads the app once OpenAmpere has started
 *  again with the restored database (#165). */
export function RestoreButton({ variant = "secondary" }: { variant?: "primary" | "secondary" }) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<"upload" | "restart" | "manual" | null>(null);
  const before = useRef<number | null>(null);

  const restore = async (chosen: File) => {
    setFile(null);
    setPhase("upload");
    try {
      before.current = (await getJson<Status>("/api/status")).database.restored?.ts ?? null;
      const result = await postFile<{ restarting: boolean }>("/api/backup/restore", chosen, "application/vnd.sqlite3");
      setPhase(result.restarting ? "restart" : "manual");
    } catch (e) {
      setPhase(null);
      toast((e as Error).message, "error");
    }
  };

  useEffect(() => {
    if (phase !== "restart") return;
    // the restored database answers with a new restore time; until then the old server or none at all
    const timer = window.setInterval(async () => {
      try {
        const status = await getJson<Status>("/api/status");
        if ((status.database.restored?.ts ?? null) !== before.current) window.location.reload();
      } catch {
        /* still starting */
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [phase]);

  return (
    <>
      <input ref={input} type="file" hidden onChange={(e) => {
        const chosen = e.target.files?.[0];
        e.target.value = "";
        if (chosen) setFile(chosen);
      }} />
      <Button variant={variant} busy={phase === "upload"} onClick={() => input.current?.click()}>
        {t("settings.restoreCard.chooseBackup")}
      </Button>
      {phase === "manual" && <Notice kind="warn">{t("settings.restoreCard.restartManually")}</Notice>}
      {file && (
        <Dialog title={t("settings.restoreCard.confirmTitle")} confirm={t("settings.restoreCard.restore")} danger
          onConfirm={() => void restore(file)} onCancel={() => setFile(null)}>
          <p>{t("settings.restoreCard.confirmReplace", { name: file.name })}</p>
          <p className="hint">{t("settings.restoreCard.confirmKept")}</p>
        </Dialog>
      )}
      {(phase === "upload" || phase === "restart") && (
        <div className="overlay">
          <div className="dialog" role="dialog" aria-modal="true" aria-label={t("settings.restoreCard.title")}>
            <h2>{t("settings.restoreCard.title")}</h2>
            <p>{phase === "upload" ? t("settings.restoreCard.checking") : t("settings.restoreCard.restarting")}</p>
            <p className="hint">{t("settings.restoreCard.dontClose")}</p>
          </div>
        </div>
      )}
    </>
  );
}

/** The last restore: when, and where the database it replaced is kept. */
export function LastRestore({ restored }: { restored: Status["database"]["restored"] }) {
  if (!restored) return null;
  return (
    <p className="hint">
      {tx("settings.restoreCard.lastRestore", { date: updatedLabel(restored.ts),
        file: <code className="code-inline wrap">{restored.kept}</code> })}
    </p>
  );
}

/** The database could not be read at the start and OpenAmpere began with an empty one: what happened and what to
 *  do, on every screen until the owner hides it. */
export function DatabaseNotice({ status, auth }: { status: Status; auth: AuthStatus | null }) {
  const damaged = status.database?.damaged;
  const [hidden, setHidden] = useState(false);
  if (!damaged || hidden) return null;
  const dismiss = async () => {
    try {
      await deleteJson("/api/database/damaged");
      setHidden(true);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <div className="page top-notice">
      <Notice kind="error">
        <p><strong>{t("shell.databaseNotice.title")}</strong></p>
        <p>{t("shell.databaseNotice.whatHappened", { date: updatedLabel(damaged.ts) })}</p>
        <p>{tx("shell.databaseNotice.fileKept", { file: <code className="code-inline wrap">{damaged.file}</code> })}</p>
        {auth && !auth.configured
          ? <p><strong>{t("shell.databaseNotice.setPasswordNow")}</strong></p>
          : <p>{t("shell.databaseNotice.restoreHint")}</p>}
        {auth?.authenticated && (
          <div className="button-row">
            <RestoreButton variant="primary" />
            <Button variant="secondary" onClick={() => void dismiss()}>{t("shell.databaseNotice.hide")}</Button>
          </div>
        )}
      </Notice>
    </div>
  );
}
