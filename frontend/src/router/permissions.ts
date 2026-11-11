import type { UserRole } from "../types/auth.ts";

export function homeRouteFor(role: UserRole): "management" | "rooms" {
  return role === "admin" ? "management" : "rooms";
}

export function roleAllowed(
  role: UserRole,
  allowedRoles?: readonly UserRole[],
): boolean {
  return allowedRoles === undefined || allowedRoles.includes(role);
}

declare module "vue-router" {
  interface RouteMeta {
    guestOnly?: boolean;
    requiresAuth?: boolean;
    roles?: readonly UserRole[];
  }
}
