import { Link } from "react-router-dom";
import { EmptyState } from "../components/ui.jsx";

export default function NotFound() {
  return (
    <div className="card">
      <EmptyState title="페이지를 찾을 수 없습니다" action={<Link className="btn btn-primary" to="/">추천 공고로 이동</Link>} />
    </div>
  );
}
