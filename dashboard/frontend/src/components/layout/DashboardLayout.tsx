import type { ReactNode } from "react";
import { useOverflowFade } from "../../hooks/useOverflowFade";
import { Header } from "./Header";
import { StatusBar } from "./StatusBar";

export function DashboardLayout({ children }: { children: ReactNode }) {
  // Fade scroll edges only where content truly overflows.
  useOverflowFade();

  return (
    <div className="flex flex-col h-screen">
      <Header />
      <main className="flex-1 overflow-auto p-3 space-y-3">{children}</main>
      <StatusBar />
    </div>
  );
}
