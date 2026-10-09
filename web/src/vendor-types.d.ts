// dmn-js ships no TypeScript declarations; the viewer is used through a narrow API.
declare module "dmn-js/lib/NavigatedViewer" {
  export default class DmnViewer {
    constructor(options: { container: HTMLElement });
    importXML(xml: string): Promise<unknown>;
    destroy(): void;
  }
}
