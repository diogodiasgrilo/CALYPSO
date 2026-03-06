import type { ReactNode } from "react";
import { Header } from "./Header";
import { StatusBar } from "./StatusBar";

export function DashboardLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col h-screen">
      <Header />
      <main className="flex-1 overflow-auto p-3 space-y-3">{children}</main>
      <StatusBar />
    </div>
  );
}
