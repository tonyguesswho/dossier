// Root route: signed-in users land directly in the library.
//
// Clerk middleware (middleware.ts) treats / as a public route, so unauthenticated
// visitors reach this component without redirect. The redirect() call below then
// sends them to /investigations, which IS protected — the middleware intercepts
// and bounces them to Clerk's hosted /sign-in. Signed-in users skip that bounce
// and land directly in the library.
//
// Rejected alternatives:
//   - Render a marketing page at /: out of scope for a 14-day capstone with no public product.
//   - Show library UI inline here: bypasses the /investigations route contract (UI-SPEC §2).
import { redirect } from "next/navigation";

export default function Home() {
  redirect("/investigations");
}
