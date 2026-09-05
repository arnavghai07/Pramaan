"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/components/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ROLE_LABEL } from "@/lib/auth";

/**
 * Console header: product mark, role-aware navigation, signed-in officer,
 * sign-out.
 *
 * "Role-aware" here means the menu matches what the account can actually
 * do, so an inspector is not shown a control that will 403. It is not a
 * security control — every restricted action is enforced server-side in
 * api/auth.py, and hiding a link has never stopped anyone from typing a
 * URL.
 */
export function AppNav() {
  const { user, logout } = useAuth();
  const pathname = usePathname();

  // The sign-in page is its own full-screen surface; a nav bar offering
  // links that all bounce back to it would be noise.
  if (!user || pathname === "/login") return null;

  const links = [
    { href: "/", label: "New inspection" },
    { href: "/dashboard", label: "Dashboard" },
    { href: "/inspections", label: "History" },
  ];

  return (
    <header className="border-b">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
        <Link href="/" className="text-base font-bold tracking-tight">
          PRAMAAN
        </Link>

        <nav className="flex items-center gap-1">
          {links.map((link) => {
            const active =
              link.href === "/"
                ? pathname === "/"
                : pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={`rounded-lg px-2.5 py-1.5 text-sm transition-colors ${
                  active
                    ? "bg-muted font-medium text-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          <div className="hidden text-right sm:block">
            <div className="text-sm leading-tight font-medium">
              {user.full_name ?? user.username}
            </div>
            <div className="text-xs leading-tight text-muted-foreground">
              {ROLE_LABEL[user.role]}
            </div>
          </div>
          {user.role === "ADMIN" && <Badge variant="secondary">Admin</Badge>}
          <Button variant="outline" size="sm" onClick={logout}>
            Sign out
          </Button>
        </div>
      </div>
    </header>
  );
}
