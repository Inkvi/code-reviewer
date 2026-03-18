import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface Props {
  content: string;
}

export default function MarkdownView({ content }: Props) {
  return (
    <div className="prose prose-sm max-w-none prose-dark">
      <Markdown remarkPlugins={[remarkGfm]}>{content}</Markdown>
    </div>
  );
}
