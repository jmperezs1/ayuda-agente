import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkBreaks from 'remark-breaks'
import remarkGfm from 'remark-gfm'
import type { Components } from 'react-markdown'

/**
 * Cómo se pinta cada etiqueta dentro de una burbuja.
 *
 * Van una a una y no con un plugin de tipografía porque las burbujas son
 * estrechas —85 % de una columna de 768 px, y menos en un teléfono— y los
 * tamaños de `prose` están pensados para un artículo: ahí dentro los títulos
 * salen enormes y los márgenes se comen media burbuja. Aquí todo se mide en
 * `em`, así que la escala nace del `text-sm` de la burbuja y no de la raíz.
 *
 * El espacio entre bloques lo pone el contenedor con `gap`, no los márgenes de
 * cada elemento: así ningún bloque abre la burbuja con un hueco arriba.
 */
const components: Components = {
    // Los títulos de un chat son subtítulos: la burbuja ya es el título. Se
    // separan un poco más de lo que hay encima porque abren sección.
    h1: ({ children }) => <h2 className="text-fg mt-1 text-[1.05em] font-semibold first:mt-0">{children}</h2>,
    h2: ({ children }) => <h3 className="text-fg mt-1 text-[1.05em] font-semibold first:mt-0">{children}</h3>,
    h3: ({ children }) => <h4 className="text-fg mt-1 font-semibold first:mt-0">{children}</h4>,
    h4: ({ children }) => <h5 className="text-fg mt-1 font-semibold first:mt-0">{children}</h5>,
    h5: ({ children }) => <h6 className="text-fg mt-1 font-semibold first:mt-0">{children}</h6>,
    h6: ({ children }) => <h6 className="text-fg mt-1 font-semibold first:mt-0">{children}</h6>,

    // El cuerpo de la burbuja es `text-fg-muted`; las negritas suben a `text-fg`
    // para que destaquen sobre él, que es justo para lo que el agente las usa.
    strong: ({ children }) => <strong className="text-fg font-semibold">{children}</strong>,
    em: ({ children }) => <em className="italic">{children}</em>,
    del: ({ children }) => <del className="text-fg-faint line-through">{children}</del>,

    // Los enlaces del agente apuntan fuera (una publicación, un teléfono, un
    // mapa): se abren en otra pestaña para no tirar la conversación.
    a: ({ href, children }) => (
        <a
            href={href}
            target="_blank"
            rel="noreferrer noopener"
            className="text-brand underline underline-offset-2"
        >
            {children}
        </a>
    ),

    ul: ({ children }) => <ul className="marker:text-fg-faint list-disc space-y-1 pl-5">{children}</ul>,
    ol: ({ children }) => <ol className="marker:text-fg-faint list-decimal space-y-1 pl-5">{children}</ol>,
    // Una lista suelta (con línea en blanco entre puntos) mete un <p> por punto;
    // sin esto cada uno abriría un párrafo aparte dentro de su propia viñeta.
    // Y un punto con casilla se queda sin viñeta: la casilla ya la hace.
    li: ({ children }) => <li className="has-[>input]:list-none [&>p]:inline">{children}</li>,
    // Casillas de GFM (`- [x] hecho`): se enseñan, no se tocan.
    input: ({ type, checked }) =>
        type === 'checkbox' ? (
            <input
                type="checkbox"
                checked={checked}
                readOnly
                aria-hidden
                className="accent-brand-strong mr-1.5 align-middle"
            />
        ) : null,

    blockquote: ({ children }) => (
        <blockquote className="border-line text-fg-subtle border-l-2 pl-3">{children}</blockquote>
    ),
    hr: () => <hr className="border-line" />,

    // `code` no dice si es de línea o de bloque (react-markdown ya no pasa
    // `inline`), así que el estilo por defecto es el de línea —la píldora— y el
    // bloque lo deshace desde `pre`, que sí sabe dónde está.
    code: ({ children }) => (
        <code className="bg-surface-muted text-fg rounded px-1 py-0.5 font-mono text-[0.85em]">
            {children}
        </code>
    ),
    pre: ({ children }) => (
        <pre className="border-line bg-page max-w-full overflow-x-auto rounded-lg border p-3 text-[0.85em] [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-[1em]">
            {children}
        </pre>
    ),

    // Una tabla no cabe en una burbuja: se deja rodar en horizontal dentro de su
    // propia caja en vez de estirar la burbuja fuera de la pantalla.
    table: ({ children }) => (
        <div className="border-line max-w-full overflow-x-auto rounded-lg border">
            {/* La última fila no lleva raya abajo: la pone ya el borde de la
                caja. Va aquí y no en la celda porque `last` sobre una celda es
                la última de su fila, no la de la última fila. */}
            <table className="w-full border-collapse text-[0.9em] [&_tr:last-child>td]:border-b-0">
                {children}
            </table>
        </div>
    ),
    th: ({ children }) => (
        <th className="border-line text-fg border-b px-2.5 py-1.5 text-left font-semibold">{children}</th>
    ),
    td: ({ children }) => <td className="border-line border-b px-2.5 py-1.5">{children}</td>,

    img: ({ src, alt }) => (
        <img src={src} alt={alt ?? ''} loading="lazy" className="border-line max-w-full rounded-md border" />
    )
}

interface MarkdownProps {
    text: string
}

/**
 * El texto del agente, que viene en Markdown, pintado como Markdown.
 *
 * Se renderiza en cada token mientras la respuesta se escribe, y a mitad de
 * camino el texto es Markdown incompleto: un `**` sin cerrar se lee como dos
 * asteriscos hasta que llega el segundo, y una valla de código sin cerrar ya
 * pinta el bloque. Las dos cosas se arreglan solas con el token siguiente.
 *
 * `memo` porque la lista entera se vuelve a pintar con cada token y solo cambia
 * la última burbuja: sin él se reparsearía toda la conversación cada vez.
 *
 * Sin `rehype-raw` a propósito: el HTML que venga en la respuesta se enseña como
 * texto en lugar de ejecutarse, y los `href` los sanea react-markdown.
 */
export const Markdown = memo(function Markdown({ text }: MarkdownProps) {
    return (
        <div className="flex flex-col gap-2">
            <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={components}>
                {text}
            </ReactMarkdown>
        </div>
    )
})
