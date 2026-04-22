// Clerk session validation for protected routes.
//
// Per CONTEXT.md D-24: Next.js middleware validates Clerk session; route handlers forward
// a short-lived Clerk JWT to FastAPI via lib/api-client.ts. Business logic stays in FastAPI
// per ARCHITECTURE.md §9 anti-pattern "Put business logic in Next.js route handlers" / D-23.
//
// Per UI-SPEC.md §1 Auth Gate:
//   - Sign-in / sign-up use Clerk HOSTED pages (not embedded <SignIn /> component).
//   - Public routes: /, /sign-in*, /sign-up*.
//   - All /investigations/* routes are protected — auth.protect() redirects unauthed to /sign-in.
//   - NEXT_PUBLIC_CLERK_AFTER_SIGN_IN_URL=/investigations routes fresh sessions to library.
//
// Rejected alternatives:
//   - authMiddleware (older Clerk API): deprecated in @clerk/nextjs 5+; 6.x uses clerkMiddleware.
//   - Per-page <SignedIn>/<SignedOut> wrappers: duplicates auth logic per route; middleware is
//     the single choke point every request passes through.
//   - Manual JWT verify inside middleware: redundant — Clerk SDK handles JWKS + expiry.
import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

const isPublicRoute = createRouteMatcher([
  "/",
  "/sign-in(.*)",
  "/sign-up(.*)",
]);

export default clerkMiddleware(async (auth, request) => {
  if (!isPublicRoute(request)) {
    await auth.protect();
  }
});

export const config = {
  matcher: [
    // Skip Next.js internals + static assets.
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
    // Always run middleware for API routes.
    "/(api|trpc)(.*)",
  ],
};
