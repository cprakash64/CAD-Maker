import { redirect } from "next/navigation";

// Legacy/alternate path -> canonical New Design route.
export default function NewDesignRedirect() {
  redirect("/designs/new");
}
