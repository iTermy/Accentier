import { useNavigate, useParams } from "react-router-dom";
import PracticeCore from "../components/PracticeCore";

export default function PracticePage() {
  const { itemId } = useParams();
  const navigate = useNavigate();
  return (
    <div>
      <button className="ghost small" style={{ marginBottom: 12 }} onClick={() => navigate(-1)}>
        ← Back
      </button>
      <PracticeCore itemId={Number(itemId)} />
    </div>
  );
}
