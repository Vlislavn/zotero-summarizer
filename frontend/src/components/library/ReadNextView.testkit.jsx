import { useLocation } from 'react-router-dom';
import ReadNextView from './ReadNextView.jsx';
import { isDecided } from './readNextCopy.js';
import useOpenPaper from '../../hooks/useOpenPaper.js';

// Test-only: ReadNextView with the open-paper state its page (LibraryReadNext) owns — the real hook on
// the test's router — and the resulting URL printed, so a test can assert `?open=KEY`.
export default function OpenableReadNext(props) {
  const paper = useOpenPaper(props.items.filter((row) => !isDecided(row)).map((row) => row.item_key));
  const { pathname, search } = useLocation();
  return (
    <>
      <ReadNextView {...props} openState={paper} />
      <output data-testid="location">{`${pathname}${search}`}</output>
    </>
  );
}
